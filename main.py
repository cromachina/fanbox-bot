import asyncio
import calendar
import datetime
import itertools
import json
import logging
import multiprocessing as mp
import re
import time

import aiosqlite
import discord
import httpx
import httpx_caching
import yaml
from discord.ext import commands

config_file = 'config.yml'
registry_db = 'registry.db'
fanbox_id_prog = re.compile('(\\d+)')
periodic_tasks = []

class obj:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)

def periodic(func, timeout):
    async def run():
        while True:
            try:
                await asyncio.wait_for(func(), timeout=timeout)
            except asyncio.TimeoutError as ex:
                logging.exception(ex)
                continue
            except Exception as ex:
                logging.exception(ex)
            await asyncio.sleep(timeout)
    task = asyncio.create_task(run())
    periodic_tasks.append(task)
    return task

class RateLimiter():
    def __init__(self, rate_limit_seconds):
        self.limit_lock = asyncio.Lock()
        self.rate_limit = rate_limit_seconds
        self.last_time = time.time() - self.rate_limit

    async def limit(self, task):
        async with self.limit_lock:
            await asyncio.sleep(max(self.last_time - time.time() + self.rate_limit, 0))
            result = await task
            self.last_time = time.time()
            return result

class FanboxClient:
    def __init__(self, cookies, headers) -> None:
        self.rate_limiter = RateLimiter(5)
        self.self_id = cookies['FANBOXSESSID'].split('_')[0]
        self.client = httpx.AsyncClient(base_url='https://api.fanbox.cc/', cookies=cookies, headers=headers)
        self.client = httpx_caching.CachingClient(self.client)

    async def get_payload(self, request):
        response = await self.rate_limiter.limit(request)
        if response.status_code == 401:
            raise Exception('Fanbox API reports 401 Unauthorized. session_cookies in the config file has likely been invalidated and needs to be updated. Restart the bot after updating.')
        if response.is_error:
            return None
        return json.loads(response.text)['body']

    async def get_user(self, user_id):
        return await self.get_payload(self.client.get('legacy/manage/supporter/user', params={'userId': user_id}))

    async def get_plans(self):
        return await self.get_payload(self.client.get('plan.listCreator', params={'userId': self.self_id}))

def map_dict(a, f):
    b = {}
    for kv in a.items():
        k, v = f(*kv)
        b[k] = v
    return b

def make_roles_objects(plan_roles):
    return map_dict(plan_roles, lambda k, v: (str(k), discord.Object(int(v))))

def str_values(d):
    return map_dict(d, lambda k, v: (k, str(v)))

def update_rate_limited(user_id, rate_limit, rate_limit_table):
    now = time.time()
    time_gate = rate_limit_table.get(user_id, 0)
    if now > time_gate:
        rate_limit_table[user_id] = now + rate_limit
        return False
    return True

def get_fanbox_pixiv_id(message):
    result = fanbox_id_prog.search(message)
    if result:
        return result.group(1)
    return None

def setup_logging(log_file):
    logging.basicConfig(
        format='[%(asctime)s][%(levelname)s] %(message)s',
        level=logging.INFO,
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ])
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)

def load_config(config_file):
    with open(config_file, 'r', encoding='utf-8') as f:
        config = obj(yaml.load(f, Loader=yaml.Loader))
        config.admin_role_id = discord.Object(int(config.admin_role_id))
        config.plan_roles = make_roles_objects(config.plan_roles)
        config.all_roles = list(config.plan_roles.values())
        config.cleanup = obj(config.cleanup)
        config.auto_role_update = obj(config.auto_role_update)
        config.session_cookies = str_values(config.session_cookies)
        return config

def parse_date(date_string):
    return datetime.datetime.fromisoformat(date_string)

def days_in_month(date):
    return datetime.timedelta(days=calendar.monthrange(date.year, date.month)[1])

def compress_transactions(txns):
    new_txns = []
    for _, group in itertools.groupby(txns, lambda x: x['targetMonth']):
        group = list(group)
        date = parse_date(group[0]['transactionDatetime'])
        new_txns.append({
            'fee': sum(map(lambda x: x['paidAmount'], group)),
            'date': date,
            'deltatime' : days_in_month(date),
        })
    return new_txns

def compute_last_subscription_range(txns):
    stop_date = datetime.datetime.min.replace(tzinfo=datetime.timezone.min)
    txn_range = []
    for txn in reversed(txns):
        date = txn['date']
        if stop_date < date:
            txn_range.clear()
            stop_date = date + days_in_month(date)
        else:
            diff = abs(date - stop_date)
            stop_date = date + days_in_month(date) + diff
        txn_range.append(txn)
    return txn_range, stop_date

def compute_plan_id(txns, plan_fee_lookup, current_date, leeway_days):
    txns = compress_transactions(txns)
    txn_range, stop_date = compute_last_subscription_range(txns)
    stop_date = stop_date + datetime.timedelta(days=abs(leeway_days))
    if stop_date < current_date or len(txn_range) == 0:
        return None
    # When there is only one choice, skip most of the calculation.
    fee_types = {txn['fee'] for txn in txn_range}
    if len(fee_types) == 1:
        return plan_fee_lookup.get(fee_types.pop())

    # When there are multiple choices, fill out the time table.
    # This could probably be made more efficient by using intervals, but that would
    # be more a lot more complicated to implement.
    days = [None] * abs(txn_range[0]['date'] - stop_date).days

    start_date = txn_range[0]['date']
    stop_idx = abs(start_date - current_date).days

    for fee in sorted(plan_fee_lookup.keys(), reverse=True):
        for txn in txn_range:
            if fee == txn['fee']:
                day_idx = abs(start_date - txn['date']).days
                for _ in range(txn['deltatime'].days):
                    while days[day_idx] != None:
                        day_idx += 1
                    days[day_idx] = fee

    # Remaining empty spaces will be caused by old plans that were never entered
    # into the plan fee lookup, usually because an old plan was removed.
    # Filling the empty spaces with the lowest plan will be the best effort resolution.
    days = days[max(stop_idx - 2, 0): min(stop_idx + 1, len(days) - 1)]
    min_fee = min(fee_types)
    days = [min_fee if day is None else day for day in days]

    return plan_fee_lookup.get(max(days))

async def open_database():
    db = await aiosqlite.connect(registry_db)
    await db.execute('create table if not exists user_data (pixiv_id integer not null primary key, data text)')
    await db.execute('create table if not exists member_pixiv (member_id integer not null primary key, pixiv_id integer)')
    await db.execute('create table if not exists plan_fee (fee numeric not null primary key, plan text)')
    return db

async def reset_bindings_db(db):
    db.execute('delete from member_pixiv')
    db.execute('vacuum')
    db.commit()

async def get_user_data_db(db, pixiv_id):
    cursor = await db.execute('select data from user_data where pixiv_id = ?', (pixiv_id,))
    user_data = await cursor.fetchone()
    if user_data is None:
        return None
    return json.loads(user_data[0])

async def update_user_data_db(db, pixiv_id, user_data):
    if user_data is None:
        return
    await db.execute('replace into user_data values(?, ?)', (pixiv_id, json.dumps(user_data)))
    await db.commit()

async def get_member_pixiv_id_db(db, member_id):
    cursor = await db.execute('select pixiv_id from member_pixiv where member_id = ?', (member_id,))
    result = await cursor.fetchone()
    if result is None:
        return None
    return result[0]

async def update_member_pixiv_id_db(db, member_id, pixiv_id):
    await db.execute('replace into member_pixiv values(?, ?)', (member_id, pixiv_id))
    await db.commit()

async def get_members_by_pixiv_id_db(db, pixiv_id):
    cursor = await db.execute('select member_id from member_pixiv where pixiv_id = ?', (pixiv_id,))
    result = await cursor.fetchall()
    return [r[0] for r in result]

async def delete_member_db(db, member_id):
    await db.execute('delete from member_pixiv where member_id = ?', (member_id,))
    await db.commit()

async def get_plan_fees_db(db):
    cursor = await db.execute('select * from plan_fee')
    result = await cursor.fetchall()
    return {r[0]:r[1] for r in result}

async def update_plan_fees_db(db, plan_fees):
    for k,v in plan_fees.items():
        await db.execute('replace into plan_fee values(?, ?)', (k, v))
    await db.commit()

async def get_plan_fee_lookup(fanbox_client, db):
    cached_plans = await get_plan_fees_db(db)
    latest_plans = await fanbox_client.get_plans()
    latest_plans = {plan['fee']:plan['id'] for plan in latest_plans}
    latest_plans = cached_plans | latest_plans
    await update_plan_fees_db(db, latest_plans)
    return latest_plans

def has_role(member, roles):
    if member is None:
        return False
    for role in roles:
        if member.get_role(role.id) is not None:
            return True
    return False

async def main():
    config = load_config(config_file)
    setup_logging(config.log_file)
    rate_limit_table = {}
    intents = discord.Intents.default()
    intents.members = True
    client = commands.bot.Bot(command_prefix='!', intents=intents)
    fanbox_client = FanboxClient(config.session_cookies, config.session_headers)
    plan_fee_lookup = None
    db = None

    async def fetch_member(discord_id):
        try:
            return await client.guilds[0].fetch_member(discord_id)
        except:
            return None

    def compute_role(user_data):
        if user_data is None:
            return None
        plan_id = compute_plan_id(user_data['supportTransactions'], plan_fee_lookup, datetime.datetime.now(datetime.timezone.min), config.auto_role_update.leeway_days)
        return config.plan_roles.get(plan_id)

    async def get_fanbox_user_data(pixiv_id, member=None, force_update=False):
        if pixiv_id is None:
            return None
        user_data = await get_user_data_db(db, pixiv_id)
        if not force_update:
            role = compute_role(user_data)
        if force_update or role is None or not has_role(member, [role]):
            user_data = await fanbox_client.get_user(pixiv_id)
        await update_user_data_db(db, pixiv_id, user_data)
        return user_data

    async def set_member_role(member, role):
        if member is None:
            return False
        if role is None:
            await member.remove_roles(*config.all_roles)
            return True
        elif not has_role(member, [role]):
            await member.remove_roles(*config.all_roles)
            await member.add_roles(role)
            return True
        return False

    async def update_role_check(member:discord.Member):
        if not has_role(member, config.all_roles):
            return
        pixiv_id = await get_member_pixiv_id_db(db, member.id)
        user_data = await get_fanbox_user_data(pixiv_id, member=member)
        role = compute_role(user_data)
        if await set_member_role(member, role):
            logging.info(f'Set role: member: {member} pixiv_id: {pixiv_id} role: {role}')

    async def update_role_check_all_members():
        guild = client.guilds[0]
        logging.info(f'Begin update role check: {guild.member_count} members')
        count = 0
        async for member in guild.fetch_members(limit=None):
            try:
                await update_role_check(member)
                count += 1
            except Exception as ex:
                logging.exception(ex)
        logging.info(f'End update role check: {count} checked')

    async def get_fanbox_role_with_pixiv_id(pixiv_id):
        user_data = await get_fanbox_user_data(pixiv_id, force_update=True)
        return compute_role(user_data)

    async def reset():
        guild = client.guilds[0]
        count = 0
        async for member in guild.fetch_members(limit=None):
            try:
                await member.remove_roles(*config.all_roles)
            except:
                pass
            count += 1
        await reset_bindings_db(db)
        return count

    def is_old_member(joined_at):
        return joined_at + datetime.timedelta(hours=config.cleanup.member_age_hours) <= datetime.datetime.now(joined_at.tzinfo)

    async def purge():
        guild = client.guilds[0]
        names = []
        async for member in guild.fetch_members(limit=None):
            if len(member.roles) == 1 and is_old_member(member.joined_at):
                try:
                    await member.kick(reason="Purge: No role assigned")
                    names.append(member.name)
                except:
                    pass
        if len(names) > 0:
            logging.info(f'purged {len(names)} users without roles: {names}')
        return names

    async def cleanup():
        try:
            await purge()
        except Exception as ex:
            logging.exception(ex)

    async def respond(message, condition, **kwargs):
        logging.info(f'User: {message.author}; Message: "{message.content}"; Response: {condition}')
        await message.channel.send(config.system_messages[condition].format(**kwargs))

    async def handle_access(message):
        member = await fetch_member(message.author.id)

        if not member:
            logging.info(f'User: {message.author}; Message: "{message.content}"; Not a member, ignored')
            return

        if update_rate_limited(message.author.id, config.rate_limit, rate_limit_table):
            await respond(message, 'rate_limited', rate_limit=config.rate_limit)
            return

        pixiv_id = get_fanbox_pixiv_id(message.content)

        if pixiv_id is None:
            await respond(message, 'no_id_found')
            return

        if config.strict_access:
            members = await get_members_by_pixiv_id_db(db, pixiv_id)
            if members and member.id not in members:
                await respond(message, 'id_bound', id=pixiv_id)
                return

        role = await get_fanbox_role_with_pixiv_id(pixiv_id)

        if not role:
            await respond(message, 'access_denied', id=pixiv_id)
            return

        await update_member_pixiv_id_db(db, member.id, pixiv_id)

        await set_member_role(member, role)

        await respond(message, 'access_granted')

    @client.command(name='add-user')
    async def add_user(ctx, pixiv_id, discord_id):
        member = await fetch_member(discord_id)

        if not member:
            await ctx.send(f'{discord_id} is not in the server.')
            return

        role = await get_fanbox_role_with_pixiv_id(pixiv_id)

        if not role:
            await ctx.send(f'{member} access denied.')
            return

        await update_member_pixiv_id_db(db, member.id, pixiv_id)

        await set_member_role(member, role)

        await ctx.send(f'{member} access granted.')

    @client.command(name='unbind-user-by-discord-id')
    async def unbind_user_by_discord_id(ctx, discord_id):
        pixiv_id = await get_member_pixiv_id_db(db, discord_id)
        await delete_member_db(db, discord_id)
        member = await fetch_member(discord_id)
        await set_member_role(member, None)
        if member is not None:
            member = member.name
        await ctx.send(f'unbound user {(discord_id, member)} with pixiv_id {pixiv_id}')

    @client.command(name='unbind-user-by-pixiv-id')
    async def unbind_user_by_pixiv_id(ctx, pixiv_id):
        member_ids = await get_members_by_pixiv_id_db(db, pixiv_id)
        for member_id in member_ids:
            await unbind_user_by_discord_id(ctx, member_id)

    @client.command(name='get-by-discord-id')
    async def get_by_discord_id(ctx, discord_id):
        member = await fetch_member(discord_id)
        if member is not None:
            member = member.name
        pixiv_id = await get_member_pixiv_id_db(db, discord_id)
        await ctx.send(f'member {(discord_id, member)} pixiv_id {pixiv_id}')

    @client.command(name='get-by-pixiv-id')
    async def get_by_pixiv_id(ctx, pixiv_id):
        member_ids = await get_members_by_pixiv_id_db(db, pixiv_id)
        for member_id in member_ids:
            await get_by_discord_id(ctx, member_id)

    @client.command(name='reset')
    async def _reset(ctx):
        count = await reset()
        await ctx.send(f'removed roles from {count} users')

    @client.command(name='purge')
    async def _purge(ctx):
        names = await purge()
        await ctx.send(f'purged {len(names)} users without roles: {names}')

    @client.command(name='test-id')
    async def test_id(ctx, id):
        role = await get_fanbox_role_with_pixiv_id(id)
        await ctx.send(f'Role: {role}')

    @client.event
    async def on_ready():
        if client.guilds.count > 1:
            logging.warning('This bot has been invited to more than 1 server. The bot may not work correctly.')
        logging.info(f'{client.user} has connected to Discord!')
        
        if config.cleanup.run:
            periodic(cleanup, config.cleanup.period_hours * 60 * 60)

        if config.auto_role_update.run:
            periodic(update_role_check_all_members, config.auto_role_update.period_hours * 60 * 60)

    @client.event
    async def on_message(message):
        if (message.author == client.user
            or message.channel.type != discord.ChannelType.private
            or message.content == ''):
            return

        try:
            is_admin = has_role(await fetch_member(message.author.id), [config.admin_role_id])
            if config.operator_mode and not is_admin:
                return
            if is_admin and message.content.startswith('!'):
                await client.process_commands(message)
            else:
                await handle_access(message)

        except Exception as ex:
            logging.exception(ex)
            await respond(message, 'system_error')

    def check_plans():
        configured_plans = set(config.plan_roles.keys())
        fanbox_plans = set(plan_fee_lookup.values())
        config_missing = configured_plans - fanbox_plans
        fanbox_missing = fanbox_plans - configured_plans
        if config_missing:
            logging.warning(f'The config file contains plans that were not found on Fanbox (including deleted plans): {config_missing}')
        if fanbox_missing:
            logging.warning(f'Fanbox may contain plans (including deleted plans) that were not found in the config file: {fanbox_missing}')

    try:
        db = await open_database()
        plan_fee_lookup = await get_plan_fee_lookup(fanbox_client, db)
        check_plans()
        token = config.operator_token if config.operator_mode else config.discord_token
        await client.start(token, reconnect=False)
    except Exception as ex:
        logging.exception(ex)
    finally:
        await client.close()
        await db.close()
    delay = 10
    logging.warning(f'Disconnected: reconnecting in {delay}s')
    await asyncio.sleep(delay)

def run_main():
    asyncio.run(main())

async def db_migration():
    import pickle
    import os
    if not os.path.exists('registry.dat'):
        return
    print('Found registry.dat: Starting DB migration')
    with open('registry.dat', 'rb') as f:
        reg = pickle.load(f)
    config = load_config(main.config_file)
    client = FanboxClient(config.session_cookies, config.session_headers)
    db = await open_database()
    for discord_id, pixiv_ids in reg['discord_ids'].items():
        for pixiv_id in pixiv_ids:
            try:
                user_data = await client.get_user(pixiv_id)
            except:
                continue
            if user_data is None:
                continue
            print(f'user {discord_id} {pixiv_id}')
            await update_member_pixiv_id_db(db, discord_id, pixiv_id)
            await update_user_data_db(db, pixiv_id, user_data)
            break
    await db.close()
    os.rename('registry.dat', 'registry.dat.backup')
    print('Moved registry.dat to registry.dat.backup')
    print('DB migration finished')

if __name__ == '__main__':
    asyncio.run(db_migration())

    while True:
        # Because discord.py is not closing aiohttp clients correctly,
        # the process has to be completely restarted to get into a good state.
        # If disconnects are frequent, the periodic cleanup function may never run.
        # A new discord client could be created, but then aiohttp sockets may leak,
        # and eventually resources would be exhausted.
        p = mp.Process(target=run_main, daemon=True)
        p.start()
        p.join()
