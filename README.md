# Fanbox Discord Bot
This bot is used to automate access control for my Fanbox Discord server.

The bot accesses the Fanbox API using your Fanbox session token. This is in your browser cookies when accessing Fanbox.

## Access control
When a Discord user sends the bot their Pixiv ID number, it is checked against your Fanbox supporter list.
The user's supporter level is then matched with a Discord role ID for them to be assigned.

Only users who are currently Fanbox subscribers will appear in the Fanbox supporter list. If they unsubscribe immediately after subscribing, they will not show up in the list! This is a limitation of Fanbox.

## Other functionality
The bot can be configured to periodically purge old users without roles.

The bot can automatically update the Discord invite and Fanbox post which contains the invite link. The link in the Fanbox post should be the last line, which will be replaced with the new link.

The bot will log potential access abuse, such as when different users gain access with the same Pixiv ID. However, no automated action is taken.

## Install and configuration
- Create a Discord app and bot:
    - https://discordpy.readthedocs.io/en/stable/discord.html
- Install python:
    - https://www.python.org/downloads/
- Run `pip install -r requirements.txt` to install dependencies. Run again after pulling latest to get new dependencies.
- Copy `config-template.yml` to `config.yml`
- In `config.yml`, update all of the spots with angle brackets: `<...>`
- Run `python3 main.py`
