import argparse
import asyncio
import calendar
import datetime
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
fanbox_id_prog = re.compile('(\d+)')
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
        self.client = httpx.AsyncClient(base_url='https://api.fanbox.cc/', cookies=cookies, headers=headers)
        self.client = httpx_caching.CachingClient(self.client)

    async def get_user(self, user_id):
        response = await self.rate_limiter.limit(self.client.get('legacy/manage/supporter/user', params={'userId': user_id}))
        if response.is_error:
            if response.status_code == 401:
                raise Exception('Fanbox API reports 401 Unauthorized. session_cookies in the config file has likely been invalidated and needs to be updated. Restart the bot after updating.')
            else:
                return None
        return json.loads(response.text)['body']

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
    time_gate = rate_limit_table.get(user_id) or 0
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
    discord_logger = logging.getLogger('discord')
    discord_logger.setLevel(logging.WARNING)

def load_config(config_file):
    with open(config_file, 'r', encoding='utf-8') as f:
        config = obj(yaml.load(f, Loader=yaml.Loader))
        config.plan_roles = make_roles_objects(config.plan_roles)
        config.fallback_role = discord.Object(int(config.fallback_role))
        config.all_roles = list(config.plan_roles.values())
        config.cleanup = obj(config.cleanup)
        config.auto_derole = obj(config.auto_derole)
        config.session_cookies = str_values(config.session_cookies)
        return config

def parse_date(date_string):
    return datetime.datetime.fromisoformat(date_string)

def previous_month(date):
    date = date - datetime.timedelta(days = 1)
    return datetime.datetime(year=date.year, month=date.month)

def days_in_month(date):
    return calendar.monthrange(date.year, date.month)[1]

def compute_last_day(txns):
    stop_date = datetime.datetime.min.replace(tzinfo=datetime.timezone.min)
    for txn in reversed(txns):
        date = parse_date(txn['transactionDatetime'])
        if stop_date < date:
            stop_date = date + datetime.timedelta(days=days_in_month(date))
        else:
            diff = abs(date - stop_date)
            stop_date = date + datetime.timedelta(days=days_in_month(date)) + diff
    return stop_date

def is_user_active_supporting(user_data):
    if user_data is None:
        return False
    return user_data['supportingPlan'] is not None

def is_user_transaction_subscribed(user_data):
    if user_data is None:
        return False
    txns = user_data['supportTransactions']
    if not txns:
        return False
    last_day = compute_last_day(txns)
    return last_day >= datetime.datetime.now(last_day.tzinfo)

async def open_database():
    db = await aiosqlite.connect(registry_db)
    await db.execute('create table if not exists user_data (pixiv_id integer not null primary key, data text)')
    await db.execute('create table if not exists member_pixiv (member_id integer not null primary key, pixiv_id integer)')
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

async def get_latest_fanbox_user_data(fanbox_client, db, pixiv_id, force_update=False):
    if pixiv_id is None:
        return None
    user_data = await get_user_data_db(db, pixiv_id)
    if force_update or not is_user_transaction_subscribed(user_data):
        user_data = await fanbox_client.get_user(pixiv_id)
    await update_user_data_db(db, pixiv_id, user_data)
    return user_data

def has_role(member, roles):
    for role in roles:
        if member.get_role(role.id) is not None:
            return True
    return False

async def main(operator_mode):
    config = load_config(config_file)
    setup_logging(config.log_file)
    rate_limit_table = {}
    intents = discord.Intents.default()
    intents.members = True
    client = commands.bot.Bot(command_prefix='!', intents=intents)
    fanbox_client = FanboxClient(config.session_cookies, config.session_headers)
    lock = asyncio.Lock()
    db = None

    async def fetch_member(discord_id):
        try:
            return await client.guilds[0].fetch_member(discord_id)
        except:
            return None

    async def get_fanbox_user_data(pixiv_id, force_update=False):
        return await get_latest_fanbox_user_data(fanbox_client, db, pixiv_id, force_update)

    async def derole_member(member, pixiv_id):
        if member is None:
            return
        try:
            await member.remove_roles(*config.all_roles)
        except:
            pass
        logging.info(f'Derole: {member} {pixiv_id}')

    async def derole_check_fanbox_supporter(member:discord.Member):
        if len(member.roles) == 1:
            return
        if not has_role(member, config.all_roles):
            return
        pixiv_id = await get_member_pixiv_id_db(db, member.id)
        user_data = await get_fanbox_user_data(pixiv_id)
        if is_user_active_supporting(user_data) or is_user_transaction_subscribed(user_data):
            return
        await derole_member(member, pixiv_id)

    async def derole_check_all_fanbox_supporters():
        guild = client.guilds[0]
        logging.info(f'Begin derole check: {guild.member_count} members')
        count = 0
        async for member in guild.fetch_members(limit=None):
            try:
                await derole_check_fanbox_supporter(member)
                count += 1
            except Exception as ex:
                logging.exception(ex)
        logging.info(f'End derole check: {count} checked')

    async def get_fanbox_role_with_pixiv_id(pixiv_id):
        user_data = await get_fanbox_user_data(pixiv_id, force_update=True)
        if not user_data:
            return None
        elif is_user_active_supporting(user_data):
            return config.plan_roles.get(user_data['supportingPlan']['id'])
        elif config.allow_fallback and is_user_transaction_subscribed(user_data):
            return config.fallback_role
        else:
            return None

    async def reset():
        async with lock:
            guild = client.guilds[0]
            count = 0
            async for member in guild.fetch_members(limit=None):
                await member.remove_roles(*config.all_roles)
                count += 1
            await reset_bindings_db(db)
            return count

    def is_old_member(joined_at):
        return joined_at + datetime.timedelta(hours=config.cleanup.member_age_hours) <= datetime.datetime.now(joined_at.tzinfo)

    async def purge():
        async with lock:
            guild = client.guilds[0]
            names = []
            async for member in guild.fetch_members(limit=None):
                if len(member.roles) == 1 and is_old_member(member.joined_at):
                    await member.kick(reason="Purge: No role assigned")
                    names.append(member.name)
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

        async with lock:
            if config.clear_roles:
                await member.remove_roles(*config.all_roles)
            await member.add_roles(role)

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

        async with lock:
            if config.clear_roles:
                await member.remove_roles(*config.all_roles)
            await member.add_roles(role)

        await ctx.send(f'{member} access granted.')

    @client.command(name='unbind-user-by-discord-id')
    async def unbind_user_by_discord_id(ctx, discord_id):
        pixiv_id = await get_member_pixiv_id_db(db, discord_id)
        await delete_member_db(db, discord_id)
        member = await fetch_member(discord_id)
        await derole_member(member, pixiv_id)
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
        fanbox_role = await get_fanbox_role_with_pixiv_id(id)
        await ctx.send(f'{fanbox_role}')

    @client.event
    async def on_command_error(ctx, error):
        logging.error(error)
        await ctx.send(error)

    @client.event
    async def on_ready():
        logging.info(f'{client.user} has connected to Discord!')
        if config.cleanup.run:
            periodic(cleanup, config.cleanup.period_hours * 60 * 60)

        if config.auto_derole.run:
            periodic(derole_check_all_fanbox_supporters, config.auto_derole.period_hours * 60 * 60)

    @client.event
    async def on_message(message):
        if (message.author == client.user
            or message.channel.type != discord.ChannelType.private
            or message.content == ''):
            return

        try:
            if operator_mode and message.author.id != config.admin_id:
                return
            if message.author.id == config.admin_id and message.content.startswith('!'):
                await client.process_commands(message)
            else:
                await handle_access(message)

        except Exception as ex:
            logging.exception(ex)
            await respond(message, 'system_error')

    try:
        db = await open_database()
        token = config.operator_token if operator_mode else config.discord_token
        await client.start(token, reconnect=False)
    except Exception as ex:
        logging.exception(ex)
    finally:
        await client.close()
        await db.close()
    delay = 10
    logging.warning(f'Disconnected: reconnecting in {delay}s')
    await asyncio.sleep(delay)

def run_main(operator):
    asyncio.run(main(operator))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--operator', action='store_true')
    args = parser.parse_args()

    while True:
        # Because discord.py is not closing aiohttp clients correctly,
        # the process has to be completely restarted to get into a good state.
        # If disconnects are frequent, the periodic cleanup function may never run.
        # A new discord client could be created, but then aiohttp sockets may leak,
        # and eventually resources would be exhausted.
        p = mp.Process(target=run_main, daemon=True, args=(args.operator,))
        p.start()
        p.join()
