# NoHitterTracker
When main.py runs, the list of games for the current day are retrieved. These games are checked/updated every minute_interval_to_update minutes.

This script needs to be restarted once daily (can be done via system restart/service or a cron job) so that the next day's games are retrieved. This can be done with a cron job, or with a service file.

## Config
- `minute_interval_to_update` - integer
  - Number of minutes to wait before updating and checking for no-hitters again.
- `num_innings_to_alert` - decimal
  - Number of no-hit innings that must be pitched by a team before sending a tweet.
  - This should be in "baseball" format, ex: 6.0 for 6 full innings pitched, 6.1 for 6 innings + 1 out, 6.2 for 6 innings + 2 outs.
- `debug_mode` - boolean, true/false
  - Flag for running in debug mode. If set to true, the script will run but no tweets will be sent.

## Service File
Replace `/path/to/NoHitterTracker/main.py` with the appropriate path.

```
[Unit]
Description=Python Twitter Bot for @NoHitterTracker
Wants=network-online.target
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /path/to/NoHitterTracker/main.py
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

## auth.py
This is a template file that must be filled in with details for the bot's Twitter account. To get this info, a new application must be created in [Twitter's Developer Portal](https://developer.twitter.com/en/portal/projects-and-apps) while logged into the bot account.

The `CONSUMER_KEY` and `CONSUMER_SECRET` correspond to the API Key and Secret under the Consumer Keys section of your Twitter application.

The `ACCESS_TOKEN` and `ACCESS_SECRET` correspond to the Access Token and Secret under the Authentication Tokens section of your Twitter application. The access token must be created with read+write permissions.

## update_status.py
This script is used to update the location of the bot's Twitter profile with the bot's status. For this to work correctly, the bot must be started as a service. The location text will read as follows:
- If the bot service is active: `Status: active`
- If the bot service is inactive: `Status: inactive`
- If an error occurs: `Status: ?`
