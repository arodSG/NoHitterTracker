#!/usr/bin/python3

# No-Hitter Tracker
#   When __main__ runs, the list of games for the current day are retrieved. These games are checked/updated every minute_interval_to_update minutes.
#   This script needs to be restarted once daily (can be done via system restart/service or a cron job) so that the next day's games are retrieved.

import time
import datetime
import requests
import json
from twython import Twython, TwythonError
from auth import (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

num_innings_to_alert = None
is_debug_mode = None # Can change this to False to run the bot without sending tweets for testing purposes.

live_team_ids_tweeted = []
finished_team_ids_tweeted = []
previous_game_ids = {}
twitter = None

REG_CURRENT = '{pitcher_name} ({team_abbrv}) currently has a {game_status} against the {opposing_team} through {innings_pitched} innings.'
COMBINED_CURRENT = 'The {team_name} currently have a {game_status} against the {opposing_team} through {innings_pitched} innings.'

REG_BROKEN = '{pitcher_name} ({team_abbrv}) no longer has a no-hitter against the {opposing_team}.'
COMBINED_BROKEN = 'The {team_name} no longer have a combined no-hitter against the {opposing_team}.'

REG_FINISHED = '{pitcher_name} ({team_abbrv}) has thrown a {game_status} against the {opposing_team}.'
COMBINED_FINISHED = 'The {team_name} have thrown a {game_status} against the {opposing_team}.'

config_data = {}


class GameDetails:
    game_id = 0
    game_status = ''

    home_team_id = 0
    home_team_name = ''
    home_team_abbrv = ''
    num_home_pitchers = 0
    home_pitcher_id = ''
    home_pitching_details = {}

    away_team_id = 0
    away_team_name = ''
    away_team_abbrv = ''
    num_away_pitchers = 0
    away_pitcher_id = ''
    away_pitching_details = {}

    def __init__(self, game_id, game_status):
        self.game_id = game_id
        self.game_status = game_status
        self.set_team_pitching_details(game_status)

    def set_team_pitching_details(self, game_status):
        request_endpoint = 'http://statsapi.mlb.com/api/v1/game/' + str(self.game_id) + '/boxscore'
        
        try:
            response = requests.get(request_endpoint)
            if response.status_code == 200:
                response = response.json()
                
                self.game_status = game_status

                self.home_team_id = response['teams']['home']['team']['id']
                self.home_team_name = response['teams']['home']['team']['name']
                self.home_team_abbrv = response['teams']['home']['team']['abbreviation']
                self.num_home_pitchers = len(response['teams']['home']['pitchers'])
                self.home_pitcher_id = response['teams']['home']['pitchers'][0] if self.num_home_pitchers > 0 else 0  # to prevent list index out of range error
                self.home_pitching_details = response['teams']['home']['teamStats']['pitching']

                self.away_team_id = response['teams']['away']['team']['id']
                self.away_team_name = response['teams']['away']['team']['name']
                self.away_team_abbrv = response['teams']['away']['team']['abbreviation']
                self.num_away_pitchers = len(response['teams']['away']['pitchers'])
                self.away_pitcher_id = response['teams']['away']['pitchers'][0] if self.num_away_pitchers > 0 else 0  # to prevent list index out of range error
                self.away_pitching_details = response['teams']['away']['teamStats']['pitching']
        except requests.exceptions.RequestException as e:
            print(e)


def get_game_ids_by_date(date): # Returns a map of { game_id: game_status } for all games on the specified date.
    global previous_game_ids
    ids = {}
    params = {'sportId': 1, 'date': date}
    request_endpoint = 'http://statsapi.mlb.com/api/v1/schedule/games/'
    
    try:
        response = requests.get(request_endpoint, params)
        if response.status_code == 200:
            if response.json()['dates']:
                games = response.json()['dates'][0]['games']
                for game in games:
                    ids[game['gamePk']] = game['status']['statusCode']
            
            if ids != previous_game_ids: # Only print the ids map if it's different than the previous time this was run.
                previous_game_ids = ids
                print(ids)
    except requests.exceptions.RequestException as e:
        print(e)
    
    return ids


def get_player_name_by_id(player_id):
    player_name = ''
    request_endpoint = 'http://statsapi.mlb.com/api/v1/people/' + str(player_id)
    
    try:
        response = requests.get(request_endpoint)
        if response.status_code == 200:
            player_name = response.json()['people'][0]['fullName']
    except requests.exceptions.RequestException as e:
        print(e)
    return player_name


def build_status(message, home_team_abbrv, away_team_abbrv):
    return message + '\n#' + home_team_abbrv + "vs" + away_team_abbrv + " | #" + away_team_abbrv + "vs" + home_team_abbrv


def check_no_hitter(team_id, pitching_details, num_pitchers):
    is_no_hitter = 'none'
    innings_pitched = float(pitching_details['inningsPitched'])
    num_hits_allowed = pitching_details['hits']
    num_walks_allowed = pitching_details['baseOnBalls']
    num_batters_hit = pitching_details['hitByPitch']
    
    if num_hits_allowed == 0:
        is_perfect_game = num_walks_allowed == 0 and num_batters_hit == 0
        if is_perfect_game:
            is_no_hitter = 'combined perfect game' if num_pitchers > 1 else 'perfect game'
        else:
            is_no_hitter = 'combined no-hitter' if num_pitchers > 1 else 'no-hitter'
    elif num_hits_allowed > 0 and team_id in live_team_ids_tweeted:
        is_no_hitter = 'combined broken' if num_pitchers > 1 else 'broken'
    return is_no_hitter


def send_no_hitter_tweet(game_details, no_hitter_team, game_status, is_game_finished):
    if twitter is not None and no_hitter_team in ['home', 'away'] and game_status in ['no-hitter', 'perfect game', 'combined no-hitter', 'combined perfect game', 'broken', 'combined broken']:
        team_id = game_details.home_team_id if no_hitter_team == 'home' else game_details.away_team_id
        pitcher_name = get_player_name_by_id(
            game_details.home_pitcher_id) if no_hitter_team == 'home' else get_player_name_by_id(
            game_details.away_pitcher_id)
        team_abbrv = game_details.home_team_abbrv if no_hitter_team == 'home' else game_details.away_team_abbrv
        team_name = game_details.home_team_name if no_hitter_team == 'home' else game_details.away_team_name
        opposing_team = game_details.away_team_name if no_hitter_team == 'home' else game_details.home_team_name
        innings_pitched = game_details.home_pitching_details['inningsPitched'] if no_hitter_team == 'home' else game_details.away_pitching_details['inningsPitched']

        if float(innings_pitched) >= num_innings_to_alert:  # only create new Tweet for no-hitters that are past the num_innings_to_alert
            if team_id not in finished_team_ids_tweeted and game_status in ['broken', 'combined broken']: # Games that had a no-hitter through 6 innings but are now broken up.
                if game_status == 'broken':
                    message = REG_BROKEN.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv)
                else: # combined broken
                    message = COMBINED_BROKEN.format(team_name=team_name, opposing_team=opposing_team)
                    
                status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                
                try:
                    if not is_debug_mode:
                        twitter.update_status(status=status)
                    live_team_ids_tweeted.append(team_id)
                    finished_team_ids_tweeted.append(team_id)
                    print('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                except TwythonError as e:
                    print('An error occurred and the Tweet was not sent: ' + str(e))
            elif team_id not in live_team_ids_tweeted and not is_game_finished: # In-progress games that have a no-hitter/perfect game through 6 innings and haven't been tweeted yet.
                if game_status in ['no-hitter', 'perfect game']:
                    message = REG_CURRENT.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, game_status=game_status, opposing_team=opposing_team, innings_pitched=innings_pitched)
                else: # combined no-hitter, combined perfect game
                    message = COMBINED_CURRENT.format(team_name=team_name, game_status=game_status, opposing_team=opposing_team)
                
                status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                
                try:
                    if not is_debug_mode:
                        twitter.update_status(status=status)
                    live_team_ids_tweeted.append(team_id)
                    print('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                except TwythonError as e:
                    print('An error occurred and the Tweet was not sent: ' + str(e))
            elif is_game_finished and team_id not in finished_team_ids_tweeted: # Finished games that were a no-hitter/perfect game and haven't been tweeted yet.
                if game_status in ['no-hitter', 'perfect game']:
                    message = REG_FINISHED.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, game_status=game_status, opposing_team=opposing_team)
                else: # combined no-hitter, combined perfect game
                    message = COMBINED_FINISHED.format(team_name=team_name, game_status=game_status, opposing_team=opposing_team)
                
                status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                
                try:
                    if not is_debug_mode:
                        twitter.update_status(status=status)
                    finished_team_ids_tweeted.append(team_id)
                    print('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                except TwythonError as e:
                    print('An error occurred and the Tweet was not sent: ' + str(e))
    else:
        print('An error occurred and the Tweet was not sent.')


if __name__ == '__main__':
    # Load config file
    try:
        with open('/home/scripts/NoHitterTracker/config.json', 'r') as file:
            config_data = json.load(file)
            minute_interval_to_update = config_data['minute_interval_to_update']
            num_innings_to_alert = config_data['num_innings_to_alert']
            is_debug_mode = config_data['debug_mode']
            print('Config data successfully loaded.')
    except:
        # Defaults
        minute_interval_to_update = 3
        num_innings_to_alert = 6.0
        is_debug_mode = False
        print('Error loading config data.')
    
    print('\n---CURRENT SETTINGS---')
    print('Update interval: ' + str(minute_interval_to_update) + ' minutes')
    print('Num innings needed to alert: ' + str(num_innings_to_alert) + ' innings')
    print('Debug: ' + str(is_debug_mode))
    twitter = Twython(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

    game_details = []
    current_date = datetime.date.today().strftime('%m/%d/%Y')
    
    print('\nDate: ' + current_date)
    print('SCANNING GAMES...\n')

    game_details = []
    live_team_ids_tweeted = []
    finished_team_ids_tweeted = []
    game_ids = get_game_ids_by_date(current_date)
    
    for key, value in game_ids.items():
        game_details.append(GameDetails(key, value))

    while True:
        for game in game_details:
            if game.game_id not in finished_team_ids_tweeted:
                game_status = game_ids[game.game_id]
                game.set_team_pitching_details(game_status)

                home_no_hitter_status = check_no_hitter(game.home_team_id, game.home_pitching_details, game.num_home_pitchers)
                if home_no_hitter_status != 'none':
                    is_final = game.game_status == 'F' # status code 'F' indicates the game is Final
                    send_no_hitter_tweet(game, 'home', home_no_hitter_status, is_final)

                away_no_hitter_status = check_no_hitter(game.away_team_id, game.away_pitching_details, game.num_away_pitchers)
                if away_no_hitter_status != 'none':
                    is_final = game.game_status == 'F' # status code 'F' indicates the game is Final
                    send_no_hitter_tweet(game, 'away', away_no_hitter_status, is_final)

        time.sleep(minute_interval_to_update * 60)
        game_ids = get_game_ids_by_date(current_date) # Contains { id: game_status } mapping. This should be called every time the loop runs so the game status' are updated.
