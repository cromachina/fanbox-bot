# Fanbox Discord Bot
This bot is used to automate access control for my Fanbox Discord server.

The bot accesses the Fanbox API using your Fanbox session token. This is found in your browser cookies when accessing Fanbox.

## Access control
When a Discord user sends the bot their Pixiv ID number, it is checked against their Fanbox transaction records, which will grant appropriate access. You can simply tell the users to message their Pixiv profile link to the bot, for example `https://www.pixiv.net/users/11`, which will extract `11` and check that ID.

When `allow_fallback` is set to `True` and `fallback_role` is configured, the bot will try to determine if the unsubscribed Fanbox user had subscribed in the past, and then assign a default role to them. Because details of which Fanbox plan was purchased is not available from the API, we cannot precisely determine which role to assign. This is fine if you only have one role to assign.

## Other functionality
The bot can be configured to periodically purge old users without roles. See `cleanup` in the config.

The bot can periodically derole users that have passed the last day of their purchased Fanbox subscription. See `auto_derole` in the config.

## Admin commands
Admin commands are prefixed with `!`, for example `!reset`
- `add-user PIXIV_ID DISCORD_ID` attempt to grant access for another user. `DISCORD_ID` is the numerical ID of a user, not their user name.
- `reset` removes all roles in your config from all users. Any other roles will be ignored.
- `purge` manually runs the user purge. Any user with no roles will be kicked from the server.
- `test-id PIXIV_ID` tests if a pixiv ID can obtain a role at this moment in time. I use this for debugging.

## Install and configuration
- Create a Discord app and bot:
    - https://discordpy.readthedocs.io/en/stable/discord.html
    - Additional step: Go to your bot application settings, under the `Bot` tab, scroll down to `server members intent` and turn it on.
        - This intent is needed for the reset and purge functionality to work, even if they are not used.
    - It is easiest to invite your bot instance to your server with administrator permissions to prevent permission errors. You can try using more restrictive permissions, but good luck.
    - Only invite one instance of a running bot to one server. If you invite the bot instance to multiple servers, it will only work with the first server it can find, which might be randomly ordered. If you need a bot to run in multiple servers, then run different instances of the bot out of different directories (so that you can have a unique config for each server, and the registry files wont clash).
- Install python with pip: https://www.python.org/downloads/
- Download and extract this repository to a new directory.
- Open a command window in the directory where you downloaded this repository.
  - If you are using Windows, navigate to the target directory in File Explorer, then type `cmd` in the address bar and hit enter to open a command window there.
- Run `pip install -r requirements.txt` to install dependencies. Run again after pulling latest code to update dependencies.
- Copy `config-template.yml` to `config.yml`
- In `config.yml`, update all of the places with angle brackets: `<...>`
  - For example: `<ROLE_ID>` becomes `12345`, but not `<12345>` (remove the brackets).
- You can change any other default fields in `config.yml` as well to turn on other functionality.
- Run `python3 main.py`
- The bot must be running continually to service random requests and run periodic functions. If you do not have a continually running computer, then I recommend renting a lightweight VM on a cloud service (Google Cloud, AWS, etc.) to host your bot instance.