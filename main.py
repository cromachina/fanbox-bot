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
from discord.flags import Intents

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
            await func()
            await asyncio.sleep(timeout)
    task = asyncio.create_task(run())
    periodic_tasks.append(task)
    return task

def get_payload(response, check_error=True):
    if check_error and response.is_error:
        raise Exception('Fanbox API error', response, response.text)
    return json.loads(response.text)['body']

class FanboxClient:
    def __init__(self, cookies, headers) -> None:
        self.client = httpx.AsyncClient(base_url='https://api.fanbox.cc/', cookies=cookies, headers=headers)
        self.client = httpx_caching.CachingClient(self.client)

    async def get_user(self, user_id):
        response = await self.client.get('legacy/manage/supporter/user', params={'userId': user_id})
        if response.is_error:
            return None
        return get_payload(response, check_error=False)

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
        config.key_roles = make_roles_objects(config.key_roles)
        config.plan_roles = make_roles_objects(config.plan_roles)
        config.fallback_role = discord.Object(int(config.fallback_role))
        config.all_roles = list(config.key_roles.values()) + list(config.plan_roles.values())
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

def is_user_fanbox_subscribed(user_data):
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

async def get_user_data_db(db, pixiv_id):
    cursor = await db.execute('select data from user_data where pixiv_id = ?', (pixiv_id,))
    user_data = await cursor.fetchone()
    if user_data is not None:
        user_data = json.loads(user_data[0])
    return user_data

async def update_user_data_db(db, pixiv_id, user_data):
    if user_data is not None:
        await db.execute('replace into user_data values(?, ?)', (pixiv_id, json.dumps(user_data)))
        await db.commit()

async def get_member_pixiv_id(db, member:discord.Member):
    cursor = await db.execute('select pixiv_id from member_pixiv where member_id = ?', (member.id,))
    result = await cursor.fetchone()
    if result is not None:
        result = result[0]
    return result

async def update_member_pixiv_id(db, member:discord.Member, pixiv_id):
    await db.execute('replace into member_pixiv values(?, ?)', (member.id, pixiv_id))
    await db.commit()

async def get_latest_fanbox_user_data(fanbox_client, db, pixiv_id, force_update=False):
    user_data = await get_user_data_db(db, pixiv_id)
    if force_update or not is_user_fanbox_subscribed(user_data):
        user_data = await fanbox_client.get_user(pixiv_id)
    await update_user_data_db(db, pixiv_id, user_data)
    return user_data

async def main(operator_mode):
    config = load_config(config_file)
    setup_logging(config.log_file)
    rate_limit_table = {}
    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)
    fanbox_client = FanboxClient(config.session_cookies, config.session_headers)
    lock = asyncio.Lock()
    db = await open_database()

    async def get_fanbox_user_data(pixiv_id, force_update=False):
        return await get_latest_fanbox_user_data(fanbox_client, db, pixiv_id, force_update)

    async def derole_check_fanbox_supporter(member:discord.Member):
        if len(member.roles) == 1:
            return
        pixiv_id = await get_member_pixiv_id(db, member)
        if pixiv_id is None or not is_user_fanbox_subscribed(await get_fanbox_user_data(pixiv_id)):
            try:
                await member.remove_roles(*config.all_roles)
            except:
                pass
            logging.info(f'Derole: {member}')

    async def derole_check_all_fanbox_supporters():
        guild = client.guilds[0]
        async for member in guild.fetch_members(limit=None):
            await derole_check_fanbox_supporter(member)

    async def get_fanbox_role_with_pixiv_id(pixiv_id):
        user_data = await get_fanbox_user_data(pixiv_id, force_update=True)
        if not user_data:
            return None
        if not is_user_fanbox_subscribed(user_data):
            return None
        elif user_data['supportingPlan']:
            return config.plan_roles.get(user_data['supportingPlan']['id'])
        elif config.allow_fallback:
            return config.fallback_role if len(user_data['supportTransactions']) != 0 else None
        else:
            return None

    async def reset():
        async with lock:
            guild = client.guilds[0]
            count = 0
            async for member in guild.fetch_members(limit=None):
                await member.remove_roles(*config.all_roles)
                count += 1
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
        guild = None
        member = None

        guild = client.guilds[0]
        try:
            member = await guild.fetch_member(message.author.id)
        except:
            pass

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

        role = await get_fanbox_role_with_pixiv_id(pixiv_id)

        if not role:
            await respond(message, 'access_denied', id=pixiv_id)
            return

        await update_member_pixiv_id(db, member, pixiv_id)

        async with lock:
            if config.clear_roles:
                await member.remove_roles(*config.all_roles)
            await member.add_roles(role)

        await respond(message, 'access_granted')

    async def handle_admin(message):
        if message.content.endswith('reset'):
            count = await reset()
            await message.channel.send(f'removed roles from {count} users')
        elif message.content.endswith('purge'):
            names = await purge()
            await message.channel.send(f'purged {len(names)} users without roles: {names}')
        elif 'test-id' in message.content:
            id = message.content.split()[-1]
            fanbox_role = await get_fanbox_role_with_pixiv_id(id)
            await message.channel.send(f'fanbox {fanbox_role}')
        else:
            await message.channel.send('unknown command')

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
            if (message.author.id == config.admin_id and message.content.startswith('!')):
                await handle_admin(message)
            else:
                await handle_access(message)

        except Exception as ex:
            logging.exception(ex)
            await respond(message, 'system_error')

    try:
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
