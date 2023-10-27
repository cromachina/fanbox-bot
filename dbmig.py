import asyncio
import main
import json
import pickle

async def run():
    config = main.load_config(main.config_file)
    client = main.FanboxClient(config.session_cookies, config.session_headers)
    db = await main.open_database()
    with open('registry.dat', 'rb') as f:
        reg = pickle.load(f)
    for discord_id, pixiv_ids in reg['discord_ids'].items():
        for pixiv_id in pixiv_ids:
            user_data = await client.get_user(pixiv_id)
            if user_data is None:
                continue
            print(f'user {discord_id} {pixiv_id}')
            if main.is_user_transaction_subscribed(user_data):
                print('subscribed')
                async with db.cursor() as cur:
                    await cur.execute('replace into member_pixiv values(?, ?)', (discord_id, pixiv_id))
                    await cur.execute('replace into user_data values(?, ?)', (pixiv_id, json.dumps(user_data)))
                    await db.commit()
                break
    await db.close()

asyncio.run(run())
