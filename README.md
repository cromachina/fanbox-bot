# Fanbox Discord Bot
This bot is used to automate access control for my Fanbox Discord server.

The bot accesses the Fanbox API using your Fanbox session token. This is found in your browser cookies when accessing Fanbox.

## Access control
When a Discord user sends the bot their Pixiv ID number, it is checked against their Fanbox transaction records, which will grant appropriate access. You can simply tell the users to message their Pixiv profile link to the bot, for example `https://www.pixiv.net/users/11`, which will extract `11` and check that ID.

When `allow_fallback` is set to `True` and `fallback_role` is configured, the bot will try to determine if the unsubscribed Fanbox user had subscribed in the past, and then assign a default role to them. Because details of which Fanbox plan was purchased is not available from the API, we cannot precisely determine which role to assign. This is fine if you only have one role to assign.

When `strict_access` is set to `True`, the bot will disallow users from using the same Pixiv ID. When a user successfully authenticates, their Discord ID is "bound" to their Pixiv ID. Successfully authenticating again will update their Pixiv ID binding. The user can only be unbound by an admin command. Some users may have had to create new Discord accounts, therefore the you will have to manually resolve unbinding of their old account.

## Other functionality
The bot can be configured to periodically purge old users without roles. See `cleanup` in the config.

The bot can periodically derole users that have passed the last day of their purchased Fanbox subscription. See `auto_derole` in the config. The last day is appropraitely calculated such that a user will get a month of access time, cumulatively, from the last transaction that occurs in a specific month; Example: If a user had transactions on 6/15, 7/1, and 8/1, then the last day of their subscription is approximately 9/15.

 `auto_derole` cannot differentiate between roles or what specific plan the user was subscribed to, so if you have roles based on different plans, they will not be switched between (either the user is subscribed or not subscribed). This limitation is the same as `allow_fallback`.

If you are using an older version of the bot without `auto_derole`, and want to update to a newer version with the feature, then all of your users will be deroled immediately when you run the bot, unless you migrate the database first. Typically this is not an issue, as users can simply send their Pixiv ID to the bot to get the role again. If you want to perform a migration without lots of deroles occurring, run `dbmig.py` first (it can take a while to run), then start the bot with `auto_derole` enabled.

## Admin commands
Admin commands are prefixed with `!`, for example `!reset`
- `add-user PIXIV_ID DISCORD_ID` attempt to grant access for another user. `DISCORD_ID` is the numerical ID of a user, not their user name. This command ignores `strict_access`.
- `unbind-user-by-discord-id DISCORD_ID` remove a user's Pixiv ID binding and roles.
- `unbind-user-by-pixiv-id PIXIV_ID` unbind all users sharing the same Pixiv ID.
- `get-by-discord-id DISCORD_ID` get the Pixiv ID bound to the given user.
- `get-by-pixiv-id PIXIV_ID` get all users using the same Pixiv ID.
- `reset` removes all roles in your config from all users. Any other roles will be ignored. Unbinds all users.
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
  - If you are not using a particular feature (like `fallback_role`), you can fill it in with a dummy value, like `0`.
- You can change any other default fields in `config.yml` as well to turn on other functionality.
- Run `python3 main.py`
- The bot must be running continually to service random requests and run periodic functions. If you do not have a continually running computer, then I recommend renting a lightweight VM on a cloud service (Google Cloud, AWS, etc.) to host your bot instance.