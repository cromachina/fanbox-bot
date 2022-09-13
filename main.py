import argparse
import asyncio
import datetime
import json
import logging
import os
import pickle
import re
import sys
import time

import discord
import httpx
import yaml
from discord.flags import Intents

config_file = 'config.yml'
registry_file = 'registry.dat'
id_prog = re.compile('(\d+)')

class obj:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)

def periodic(func, timeout):
    async def run():
        while True:
            await asyncio.sleep(timeout)
            await func()
    return asyncio.create_task(run())

def get_payload(response, check_error=True):
    if check_error and response.is_error:
        raise Exception('Fanbox API error', response, response.text)
    return json.loads(response.text)['body']

class FanboxClient:
    def __init__(self, cookies, headers) -> None:
        self.client = httpx.AsyncClient(base_url='https://api.fanbox.cc/', cookies=cookies, headers=headers)

    async def get_editable_post(self, post_id):
        return get_payload(await self.client.get('post.getEditable', params={'postId': post_id}))

    async def post_update(self, data):
        return get_payload(await self.client.post('post.update', data=data))

    async def get_user(self, user_id):
        response = await self.client.get('legacy/manage/supporter/user', params={'userId': user_id})
        if response.is_error:
            return None
        return get_payload(response, check_error=False)

def update_post_invite(post, discord_invite):
    post['body']['blocks'][-1]['text'] = discord_invite

def convert_post(post):
    return { 'postId': post['id']
        , 'status': post['status']
        , 'feeRequired': post['feeRequired']
        , 'title': post['title']
        , 'body': json.dumps(post['body']['blocks'])
        , 'tags': json.dumps(post['tags'])
        , 'tt': 'a16cbb5611d546e8f4f509f9cbdf98b5' # IDK what this is, but it doesn't seem to change. A hash maybe?
    }

def make_roles_objects(plan_roles):
    return { k: discord.Object(v) for k, v in plan_roles.items() }

def update_rate_limited(user_id, rate_limit, rate_limit_table):
    now = time.time()
    time_gate = rate_limit_table.get(user_id) or 0
    if now > time_gate:
        rate_limit_table[user_id] = now + rate_limit
        return False
    return True

def get_id(message):
    result = id_prog.search(message)
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
        config.fallback_role = discord.Object(config.fallback_role)
        config.all_roles = list(config.key_roles.values()) + list(config.plan_roles.values())
        config.cleanup = obj(config.cleanup)
        return config

def load_registry():
    if not os.path.isfile(registry_file):
        return {'pixiv_ids': {}, 'discord_ids':{}}
    with open(registry_file, 'rb') as f:
        return pickle.load(f)

def save_registry(registry):
    with open(registry_file, 'wb') as f:
        pickle.dump(registry, f)

def delete_registry():
    if os.path.isfile(registry_file):
        os.remove(registry_file)

def append_field(table, field_id, set_id):
    data = table.get(field_id, set())
    data.add(set_id)
    table[field_id] = data

def update_registry(discord_id, pixiv_id):
    registry = load_registry()
    append_field(registry['pixiv_ids'], pixiv_id, discord_id)
    append_field(registry['discord_ids'], discord_id, pixiv_id)
    save_registry(registry)
    return (registry['pixiv_ids'].get(pixiv_id), registry['discord_ids'].get(discord_id))

async def main(operator_mode):
    config = load_config(config_file)
    setup_logging(config.log_file)
    rate_limit_table = {}
    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)
    fanbox_client = FanboxClient(config.session_cookies, config.session_headers)
    lock = asyncio.Lock()

    async def get_role_with_key(key):
        if config.key_mode:
            return config.key_roles.get(key)
        else:
            user = await fanbox_client.get_user(key)
            if not user:
                return None
            elif user['supportingPlan']:
                return config.plan_roles.get(user['supportingPlan']['id'])
            elif config.allow_fallback:
                return config.fallback_role if len(user['supportTransactions']) != 0 else None
            else:
                return None

    async def reset():
        async with lock:
            guild = client.guilds[0]
            count = 0
            async for member in guild.fetch_members(limit=None):
                await member.remove_roles(*config.all_roles)
                count += 1
            delete_registry()
            return count

    async def regen_invite():
        guild = client.guilds[0]
        all_invites = await guild.invites()
        invite:discord.Invite = all_invites[0]
        await invite.delete()
        return await invite.channel.create_invite(
              max_age = invite.max_age
            , max_uses = invite.max_uses
            , temporary = invite.temporary
            , unique = True
        )

    async def regen_fanbox_invite_post():
        invite = await regen_invite()
        fanbox_post = await fanbox_client.get_editable_post(config.fanbox_discord_post_id)
        update_post_invite(fanbox_post, invite.url)
        await fanbox_client.post_update(convert_post(fanbox_post))
        logging.info(f'invite regenerated: {invite.url}')

    def is_old_member(joined_at):
        return joined_at + datetime.timedelta(hours=config.cleanup.member_age_hours) <= datetime.datetime.now()

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
            names = await purge()
            if len(names) > 0 and config.cleanup.update_invite:
                await regen_fanbox_invite_post()
        except Exception as ex:
            logging.exception(ex)

    async def respond(message, condition, **kwargs):
        logging.info(f'User: {message.author}; Message: "{message.content}"; Response: {condition}')
        await message.channel.send(config.system_messages[condition].format(**kwargs))

    async def handle_access(message):
        guild = None
        member = None
        key = None

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

        key = message.content

        if not config.key_mode:
            key = get_id(message.content)

            if not key:
                await respond(message, 'no_id_found')
                return

        role = await get_role_with_key(key)

        if not role:
            await respond(message, 'access_denied')
            return

        async with lock:
            if config.clear_roles:
                await member.remove_roles(*config.all_roles)

            await member.add_roles(role)
            if not config.key_mode:
                discord_ids, pixiv_ids = update_registry(member.id, key)
                if discord_ids and len(discord_ids) > 1:
                    logging.warning(f'Pixiv ID {key} has multiple registered users:')
                    for discord_id in discord_ids:
                        try:
                            user = await client.fetch_user(discord_id)
                            logging.warning(f'    {user}')
                        except:
                            logging.warning(f'    {discord_id} (No user found)')
                if pixiv_ids and len(pixiv_ids) > 1:
                    logging.warning(f'User {member} has multiple registered pixiv IDs:')
                    for pixiv_id in pixiv_ids:
                        logging.warning(f'    {pixiv_id}')

        await respond(message, 'access_granted')

    async def handle_admin(message):
        if message.content.endswith('reset'):
            count = await reset()
            await message.channel.send(f'removed roles from {count} users')
        elif message.content.endswith('purge'):
            names = await purge()
            await message.channel.send(f'purged {len(names)} users without roles: {names}')
        elif message.content.endswith('regen-invite'):
            await regen_fanbox_invite_post()
            await message.channel.send(f'invite regenerated')
        elif 'test-id' in message.content:
            id = message.content.split()[-1]
            role = await get_role_with_key(id)
            await message.channel.send(f'{role}')
        else:
            await message.channel.send('unknown command')

    @client.event
    async def on_ready():
        logging.info(f'{client.user} has connected to Discord!')

    @client.event
    async def on_message(message):
        if (message.author == client.user
            or message.channel.type != discord.ChannelType.private
            or message.content == ''):
            return

        try:
            if (message.author.id == config.admin_id and message.content.startswith('!')):
                await handle_admin(message)
            elif not operator_mode:
                await handle_access(message)

        except Exception as ex:
            logging.exception(ex)
            await respond(message, 'system_error')

    token = config.operator_token if operator_mode else config.discord_token

    if config.cleanup.run:
        periodic(cleanup, config.cleanup.period_hours * 60 * 60)

    while True:
        try:
            await client.start(token, reconnect=False)
        except Exception as ex:
            logging.exception(ex)
        await client.logout()
        delay = 10
        logging.warning(f'Disconnected: reconnecting in {delay}s')
        # Because discord.py is not closing aiohttp clients correctly,
        # the process has to be completely restarted to get into a good state.
        # If disconnects are frequent, the periodic cleanup function may never run.
        # A new discord client could be created, but then aiohttp sockets may leak,
        # and eventually resources would be exhausted.
        os.execv(sys.executable, sys.argv)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--operator', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.operator))
