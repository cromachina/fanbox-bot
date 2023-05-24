# Fanbox Discord Bot
This bot is used to automate access control for my Fanbox Discord server.

The bot accesses the Fanbox API using your Fanbox session token. This is found in your browser cookies when accessing Fanbox.

## Access control
When a Discord user sends the bot their Pixiv ID number, it is checked against their Fanbox transaction records, which will grant appropriate access.

When `allow_fallback` is set to `True` and `fallback_role` is configured, the bot will try to determine if the unsubscribed Fanbox user had subscribed in the past, and then assign a default role to them. Because details of which Fanbox plan was purchased is not available from the API, we cannot precisely determine which role to assign. This is fine if you only have one role to assign.

## Other functionality
The bot can be configured to periodically purge old users without roles.

The bot can periodically derole users that have passed the last day of their purchased Fanbox subscription.

## Install and configuration
- Create a Discord app and bot:
    - https://discordpy.readthedocs.io/en/stable/discord.html
    - Additional step: Go to your bot application settings, under the `Bot` tab, scroll down to `server members intent` and turn it on.
        - This intent is needed for the reset and purge functionality to work, even if they are not used.
- Install python with pip:
    - https://www.python.org/downloads/
- Run `pip install -r requirements.txt` to install dependencies. Run again after pulling latest to get new dependencies.
- Copy `config-template.yml` to `config.yml`
- In `config.yml`, update all of the spots with angle brackets: `<...>`
- Run `python3 main.py`
