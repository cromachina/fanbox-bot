import argparse
import asyncio
import json
import logging
import os
import pickle
import re
import time

import discord
from discord.flags import Intents
import requests
import yaml

config_file = 'config.yml'
registry_file = 'registry.dat'
id_prog = re.compile('(\d+)')

class obj:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)

def get_supporters(cookies, headers):
    r = requests.get('https://api.fanbox.cc/relationship.listFans?status=supporter', cookies=cookies, headers=headers)
    return json.loads(r.text)['body']

def find_supporter(supporters, user_id):
    for supporter in supporters:
        user = supporter['user']
        if user['userId'] == user_id:
            return supporter
    return None

def make_roles_objects(plan_roles):
    return { k: discord.Object(v) for k, v in plan_roles.items() }

def get_role_from_supporter(supporter, plan_roles):
    if supporter:
        for plan, role in plan_roles.items():
            if supporter['planId'] == plan:
                return role
    return None

def get_role_with_key(config, key):
    if config.key_mode:
        return config.key_roles.get(key)
    else:
        supporters = get_supporters(config.session_cookies, config.session_headers)
        supporter = find_supporter(supporters, key)
        return get_role_from_supporter(supporter, config.plan_roles)

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
        config.all_roles = list(config.key_roles.values()) + list(config.plan_roles.values())
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

def main(operator_mode):
    config = load_config(config_file)
    setup_logging(config.log_file)
    rate_limit_table = {}
    intents = discord.Intents.default()
    intents.members = True
    client = discord.Client(intents=intents)
    lock = asyncio.Lock()

    async def discord_id_to_name(id):
        await client.fetch_user(id)

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
            #await respond(message, 'not_member')
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

        role = get_role_with_key(config, key)

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
            async with lock:
                guild = client.guilds[0]
                count = 0
                async for member in guild.fetch_members(limit=None):
                    await member.remove_roles(*config.all_roles)
                    count += 1
                delete_registry()
                await message.channel.send(f'removed roles from {count} users')
        elif message.content.endswith('purge'):
            async with lock:
                guild = client.guilds[0]
                count = 0
                names = []
                async for member in guild.fetch_members(limit=None):
                    if len(member.roles) == 1:
                        await member.kick(reason="Purge: No role assigned")
                        names.append(member.name)
                        count += 1
                await message.channel.send(f'purged {count} users without roles: {names}')
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
    client.run(token)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--operator', action='store_true')
    args = parser.parse_args()
    main(args.operator)
