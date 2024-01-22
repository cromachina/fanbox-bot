# Fanbox Discord Bot
This bot is used to automate access control for my Fanbox Discord server.

The bot accesses the Fanbox API using your Fanbox session token. This is found in your browser cookies when accessing Fanbox.

## Access control
When a Discord user sends the bot their Pixiv ID number, it is checked against their Fanbox transaction records, which will grant appropriate access. You can simply tell the users to message their Pixiv profile link to the bot, for example `https://www.pixiv.net/users/11`, which will extract `11` and check that ID.

When `strict_access` is set to `True`, the bot will disallow users from using the same Pixiv ID. When a user successfully authenticates, their Discord ID is "bound" to their Pixiv ID. Successfully authenticating again will update their Pixiv ID binding. The user can only be unbound by an admin command. Some users may have had to create new Discord accounts, therefore the you will have to manually resolve unbinding of their old account.

## Other functionality

### Auto Purge
The bot can be configured to periodically purge old users without roles. See `cleanup` in the config.

### Auto Role Update
The bot can be configured to periodically update a user's role based on their Fanbox subscription. See `auto_role_update` in the config. The subscription is checked whenever the bot thinks the subscription is going to change. Because this is based on the last cached data of the user, if the user wants their role to be updated immediately (such as to a higher role), then they need to submit their Pixiv ID to the bot again.

#### Period of role assignment
The bot will make the best effort to assign the correct role based on the user's previous recent transactions, as well as ensure that they get to retain the role for the contiguous overflow days since making those transactions. For example: If a user had subscribed on 6/15, 7/1, and 8/1, then the last day of their subscription is approximately 9/15.

#### Determining role assignment
The highest role assigned is determined like so: A calendar month's transactions for a user are summed up and replaced by the last transaction in that month, so if they had two 500 yen transactions on 6/10 and 6/15, then this would be represented by one 1000 yen transaction on 6/15. Then, for contiguous months of transactions, the days in that period of time are filled starting with the highest roles first, for a month worth of time, in the positions each transaction starts, or the next available position that can be filled. This is easier to demonstrate with the following graphs:

![image](https://github.com/cromachina/fanbox-bot/assets/82557197/8e1e4414-5bdb-42cc-a1f9-f4d6e693e509)

#### Why transactions?
Transactions are used to determine roles because this is the only historical information that the Fanbox API provides. Unfortunately Fanbox does not provide what specific plan was purchased with a given transaction, which makes determining which role to assign more complicated. This also means that plans should be uniquely determined by their price.

#### Adding or removing plans from Fanbox
Each time the bot starts, plans are retrieved from Fanbox and cached. If you removed a plan from your Fanbox, you should still keep the plan in your `plan_roles` setting so that a user can still be granted the last valid role that plan represented. When no more users have that role, you could then remove that plan from the `plan_roles` setting without impacting user experience.

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
- The bot must be running continually to service random requests and run periodic functions. If you do not have a continually running computer, then I recommend renting a lightweight VM on a cloud service (Google Cloud, AWS, DigitalOcean, etc.) to host your bot instance.
- I recommend installing Docker to run the bot, to both mitigate build issues and have your bot start automatically if your computer or VM restarts.
  - For Windows: https://www.docker.com/products/docker-desktop/
  - If using a cloud VM, typically Debian or Ubuntu Linux: run `sudo apt update && sudo apt install docker`
- Download (or clone) and extract this repository to a new directory.
- Copy `config-template.yml` to `config.yml`
- In `config.yml`, update all of the places with angle brackets: `<...>`
  - For example: `<ROLE_ID>` becomes `12345`, but not `<12345>` (remove the brackets).
  - If you are not using a particular feature, you can fill it in with a dummy value, like `0`.
- You can change any other default fields in `config.yml` as well to turn on other functionality.
- To start the bot, run `docker compose up -d` in the bot directory.
- To stop, run `docker compose down` in the bot directory.
- Logs are written to `log.txt`, or you can view output with Docker `docker compose logs --follow`

## Updating the bot
- Stop the bot `docker compose down`
- Download the latest version
- Run `docker compose build` to update dependencies
- Start the bot `docker compose up -d`
