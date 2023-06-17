#!/usr/bin/python3

# No-Hitter Tracker
#   When __main__ runs, the list of games for the current day are retrieved. These games are checked/updated every minute_interval_to_update minutes.
#   This script needs to be restarted once daily (can be done via system restart/service or a cron job) so that the next day's games are retrieved.

import os
import logging
import time
import datetime
import requests
import json
from twython import Twython, TwythonError
from auth import (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

num_innings_to_alert = None
is_debug_mode = None # Can change this to False to run the bot without sending tweets for testing purposes.

live_team_ids_tweeted = {} # {team_id: {isPerfectGame: False, isFinished: True}}
finished_team_ids_tweeted = []
previous_game_ids = {}
twitter = None

REG_CURRENT = '{pitcher_name} ({team_abbrv}) currently has a {game_status} against the {opposing_team} through {innings_pitched} innings.'
COMBINED_CURRENT = 'The {team_name} currently have a {game_status} against the {opposing_team} through {innings_pitched} innings.'

REG_DOWNGRADE = '{pitcher_name} ({team_abbrv}) no longer has a perfect game against the {opposing_team}. No-hitter is still active.'
COMBINED_DOWNGRADE = 'The {team_name} no longer have a combined perfect game against the {opposing_team}. No-hitter is still active.'

REG_BROKEN = '{pitcher_name} ({team_abbrv}) no longer has a no-hitter against the {opposing_team}.'
COMBINED_BROKEN = 'The {team_name} no longer have a combined no-hitter against the {opposing_team}.'

BROKEN_BY = 'Broken up by {batter_name} after {inning}.{outs} innings.'

REG_FINISHED = '{pitcher_name} ({team_abbrv}) has thrown a {game_status} against the {opposing_team}.'
COMBINED_FINISHED = 'The {team_name} have thrown a {game_status} against the {opposing_team}.'

config_data = {}


class PlayDetails:
    def __init__(self, play_details, is_hit):
        self.description = play_details['result']['description']
        self.batter_name = play_details['matchup']['batter']['fullName']
        self.pitcher_name = play_details['matchup']['pitcher']['fullName']
        self.completed_innings = play_details['about']['inning'] - 1 # Subtract 1 for completed innings; 2 outs in the 7th is 6.2 innings pitched
        self.completed_outs = play_details['count']['outs']
        self.is_hit = is_hit
        self.is_walk_or_error = play_details['result']['eventType'] in ['walk', 'error']


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
    
    def __init__(self, game_id):
        self.game_id = game_id
        self.home_pitcher_broken_play = None
        self.home_pitcher_downgrade_play = None
        self.away_pitcher_broken_play = None
        self.away_pitcher_downgrade_play = None
        self.set_live_game_details()
    
    def set_live_game_details(self):
        request_endpoint = 'http://statsapi.mlb.com/api/v1.1/game/' + str(self.game_id) + '/feed/live'
        
        try:
            response = requests.get(request_endpoint)
            if response.status_code == 200:
                response = response.json()
                
                game_data = response['gameData']
                status = game_data['status']
                home_team_details = game_data['teams']['home']
                away_team_details = game_data['teams']['away']
                
                live_data = response['liveData']
                boxscore = live_data['boxscore']
                home_team_boxscore = boxscore['teams']['home']
                away_team_boxscore = boxscore['teams']['away']

                self.game_status = status['statusCode']
                self.plays = live_data['plays']
                
                self.home_team_id = home_team_details['id']
                self.home_team_name = home_team_details['name']
                self.home_team_abbrv = home_team_details['abbreviation']
                self.num_home_pitchers = len(home_team_boxscore['pitchers'])
                self.home_pitcher_id = home_team_boxscore['pitchers'][0] if self.num_home_pitchers > 0 else 0  # to prevent list index out of range error
                self.home_pitching_details = home_team_boxscore['teamStats']['pitching']

                self.away_team_id = away_team_details['id']
                self.away_team_name = away_team_details['name']
                self.away_team_abbrv = away_team_details['abbreviation']
                self.num_away_pitchers = len(away_team_boxscore['pitchers'])
                self.away_pitcher_id = away_team_boxscore['pitchers'][0] if self.num_away_pitchers > 0 else 0  # to prevent list index out of range error
                self.away_pitching_details = away_team_boxscore['teamStats']['pitching']
        except requests.exceptions.RequestException as e:
            logging.exception(e)
        except ConnectionError as e:
            logging.exception(e)
    
    def set_broken_details(self):
        if self.home_pitcher_broken_play == None or self.away_pitcher_broken_play == None:
            all_plays = self.plays['allPlays']
        
            for play_details in all_plays:
                play_result = play_details['result']
                play_type = play_result['type']
                play_event_type = play_result['eventType']
                is_out = play_result['isOut']
                is_top_inning = play_details['about']['isTopInning']
                is_walk_or_error = play_event_type in ['walk', 'error']
                is_hit = play_type == 'atBat' and not is_out and play_event_type not in ['walk', 'error', 'fielders_choice']
                
                if self.home_pitcher_broken_play == None and is_top_inning:
                    if is_hit:
                        self.home_pitcher_broken_play = PlayDetails(play_details, is_hit)
                    elif self.home_pitcher_downgrade_play == None and is_walk_or_error:
                        self.home_pitcher_downgrade_play = PlayDetails(play_details, is_hit)
                elif self.away_pitcher_broken_play == None and not is_top_inning:
                    if is_hit:
                        self.away_pitcher_broken_play = PlayDetails(play_details, is_hit)
                    elif self.away_pitcher_downgrade_play == None and is_walk_or_error:
                        self.away_pitcher_downgrade_play = PlayDetails(play_details, is_hit)


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
                logging.debug('get_game_ids_by_date:: ids: ' + str(ids))
    except requests.exceptions.RequestException as e:
        logging.exception(e)
    except ConnectionError as e:
        logging.exception(e)
    
    return ids


def get_player_name_by_id(player_id):
    player_name = ''
    request_endpoint = 'http://statsapi.mlb.com/api/v1/people/' + str(player_id)
    
    try:
        response = requests.get(request_endpoint)
        if response.status_code == 200:
            player_name = response.json()['people'][0]['fullName']
    except requests.exceptions.RequestException as e:
        logging.exception(e)
    except ConnectionError as e:
        logging.exception(e)
    return player_name


def build_status(message, home_team_abbrv, away_team_abbrv):
    return message + '\n\n#' + home_team_abbrv + "vs" + away_team_abbrv + " | #" + away_team_abbrv + "vs" + home_team_abbrv


def check_no_hitter(team_id, pitching_details, num_pitchers):
    status = 'none'
    is_combined = num_pitchers > 1
    innings_pitched = float(pitching_details['inningsPitched'])
    num_hits_allowed = pitching_details['hits']
    num_walks_allowed = pitching_details['baseOnBalls']
    num_batters_hit = pitching_details['hitByPitch']
    
    if num_hits_allowed == 0:
        is_perfect_game = num_walks_allowed == 0 and num_batters_hit == 0
        if is_perfect_game:
            status = 'combined perfect game' if is_combined else 'perfect game'
        elif team_id in live_team_ids_tweeted and live_team_ids_tweeted[team_id]['isPerfectGame']:
            status = 'combined downgrade' if is_combined else 'downgrade'
        else:
            status = 'combined no-hitter' if is_combined else 'no-hitter'
    elif num_hits_allowed > 0 and team_id in live_team_ids_tweeted:
        status = 'combined broken' if is_combined else 'broken'
    return status


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
        
        SEND_NO_HITTER_TWEET_LOG = 'send_no_hitter_tweet:: game_id: {0}, game_status: {1}, team_id: {2}, pitcher_name: {3}, team_abbrv: {4}, team_name: {5}, opposing_team: {6}, innings_pitched: {7}'
        logging.debug(SEND_NO_HITTER_TWEET_LOG.format(game_details.game_id, game_status, team_id, pitcher_name, team_abbrv, team_name, opposing_team, innings_pitched))

        if float(innings_pitched) >= num_innings_to_alert:  # only create new Tweet for no-hitters that are past the num_innings_to_alert
            if team_id in live_team_ids_tweeted and team_id not in finished_team_ids_tweeted:
                if game_status in ['broken', 'combined broken', 'downgrade', 'combined downgrade']: # Games that had a no-hitter through 6 innings but are now broken up.
                    game_details.set_broken_details()
                    
                    if game_status in ['broken', 'combined broken']:
                        broken_play_details = game_details.home_pitcher_broken_play if no_hitter_team == 'home' else game_details.away_pitcher_broken_play
                        broken_by_message = BROKEN_BY.format(batter_name=broken_play_details.batter_name, inning=broken_play_details.completed_innings, outs=broken_play_details.completed_outs)
                        
                        if game_status == 'broken':
                            message = REG_BROKEN.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, opposing_team=opposing_team) + '\n\n' + broken_by_message
                        elif game_status == 'combined broken':
                            message = COMBINED_BROKEN.format(team_name=team_name, opposing_team=opposing_team) + '\n\n' + broken_by_message
                    else: # downgrade
                        downgrade_play_details = game_details.home_pitcher_downgrade_play if no_hitter_team == 'home' else game_details.away_pitcher_downgrade_play
                        
                        if game_status == 'downgrade':
                            message = REG_DOWNGRADE.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, opposing_team=opposing_team)
                        elif game_status == 'combined downgrade':
                            message = COMBINED_DOWNGRADE.format(team_name=team_name, opposing_team=opposing_team)
                    
                    status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                    
                    try:
                        if not is_debug_mode:
                            twitter.update_status(status=status)
                        live_team_ids_tweeted[team_id]['isPerfectGame'] = False
                        if game_status in ['broken', 'combined broken']: # Only set game finished if broken; don't set finished for PG downgrades.
                            live_team_ids_tweeted[team_id]['isFinished'] = True
                            finished_team_ids_tweeted.append(team_id)
                        logging.info('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                    except TwythonError as e:
                        logging.exception('An error occurred and the Tweet was not sent: ' + str(e))
            elif team_id not in live_team_ids_tweeted and not is_game_finished: # In-progress games that have a no-hitter/perfect game through 6 innings and haven't been tweeted yet.
                if game_status in ['no-hitter', 'perfect game']:
                    message = REG_CURRENT.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, game_status=game_status, opposing_team=opposing_team, innings_pitched=innings_pitched)
                else: # combined no-hitter, combined perfect game
                    message = COMBINED_CURRENT.format(team_name=team_name, game_status=game_status, opposing_team=opposing_team, innings_pitched=innings_pitched)
                
                status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                
                try:
                    if not is_debug_mode:
                        twitter.update_status(status=status)
                    isPerfectGame = game_status in ['perfect game', 'combined perfect game']
                    live_team_ids_tweeted[team_id] = {'isPerfectGame': isPerfectGame, 'isFinished': False}
                    logging.info('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                except TwythonError as e:
                    logging.exception('An error occurred and the Tweet was not sent: ' + str(e))
            elif is_game_finished and team_id not in finished_team_ids_tweeted: # Finished games that were a no-hitter/perfect game and haven't been tweeted yet.
                if game_status in ['no-hitter', 'perfect game']:
                    message = REG_FINISHED.format(pitcher_name=pitcher_name, team_abbrv=team_abbrv, game_status=game_status, opposing_team=opposing_team)
                else: # combined no-hitter, combined perfect game
                    message = COMBINED_FINISHED.format(team_name=team_name, game_status=game_status, opposing_team=opposing_team)
                
                status = build_status(message, game_details.home_team_abbrv, game_details.away_team_abbrv)
                
                try:
                    if not is_debug_mode:
                        twitter.update_status(status=status)
                    live_team_ids_tweeted[team_id]['isFinished'] = True
                    finished_team_ids_tweeted.append(team_id)
                    logging.info('Tweet sent: ' + message + ' (Game ID: ' + str(game_details.game_id) + ')')
                except TwythonError as e:
                    logging.exception('An error occurred and the Tweet was not sent: ' + str(e))
    else:
        logging.error('An error occurred and the Tweet was not sent.')


if __name__ == '__main__':
    logging.addLevelName(logging.DEBUG, 'DBG')
    logging.addLevelName(logging.WARNING, 'WRN')
    logging_filename = '/home/scripts/NoHitterTracker/nohittertracker.log'
    logging.basicConfig(filename=logging_filename, level=logging.INFO, format='%(asctime)s - [%(levelname).3s] %(message)s')
    
    # Load config file
    try:
        with open('/home/scripts/NoHitterTracker/config.json', 'r') as file:
            config_data = json.load(file)
            minute_interval_to_update = config_data['minute_interval_to_update']
            num_innings_to_alert = config_data['num_innings_to_alert']
            is_debug_mode = config_data['debug_mode']
            logging.info('Config data successfully loaded.')
    except:
        # Defaults
        minute_interval_to_update = 3
        num_innings_to_alert = 6.0
        is_debug_mode = False
        logging.exception('Error loading config data.')
    
    logging.info('\n---CURRENT SETTINGS---')
    logging.info('Update interval: ' + str(minute_interval_to_update) + ' minutes')
    logging.info('Num innings needed to alert: ' + str(num_innings_to_alert) + ' innings')
    logging.info('Debug: ' + str(is_debug_mode))
    twitter = Twython(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

    game_details = []
    current_date = datetime.date.today().strftime('%m/%d/%Y')

    logging.info('\nDate: ' + current_date)
    logging.info('SCANNING GAMES...\n')

    game_details = []
    game_ids = get_game_ids_by_date(current_date)

    for key, value in game_ids.items():
        game_details.append(GameDetails(key))

    while True:
        for game in game_details:
            game.game_status = game_ids[game.game_id]
            live_no_hitter_tweeted = game.home_team_id in live_team_ids_tweeted or game.away_team_id in live_team_ids_tweeted
            finished_no_hitter_tweeted = game.home_team_id in finished_team_ids_tweeted or game.away_team_id in finished_team_ids_tweeted
            
            if game.game_status == 'I' or (game.game_status == 'F' and live_no_hitter_tweeted and not(finished_no_hitter_tweeted)):
                game.set_live_game_details()

                home_no_hitter_status = check_no_hitter(game.home_team_id, game.home_pitching_details, game.num_home_pitchers)
                logging.debug('main:: game_id: ' + str(game.game_id) + ', home_no_hitter_status: ' + home_no_hitter_status + ', home_team_id: ' + str(game.home_team_id) + ', num_home_pitchers: ' + str(game.num_home_pitchers))
                if home_no_hitter_status != 'none':
                    is_final = game.game_status == 'F' # status code 'F' indicates the game is Final
                    send_no_hitter_tweet(game, 'home', home_no_hitter_status, is_final)

                away_no_hitter_status = check_no_hitter(game.away_team_id, game.away_pitching_details, game.num_away_pitchers)
                logging.debug('main:: game_id: ' + str(game.game_id) + ', away_no_hitter_status: ' + away_no_hitter_status + ', away_team_id: ' + str(game.away_team_id) + ', num_away_pitchers: ' + str(game.num_away_pitchers))
                if away_no_hitter_status != 'none':
                    is_final = game.game_status == 'F' # status code 'F' indicates the game is Final
                    send_no_hitter_tweet(game, 'away', away_no_hitter_status, is_final)

        time.sleep(minute_interval_to_update * 60)
        game_ids = get_game_ids_by_date(current_date) # Contains { id: game_status } mapping. This should be called every time the loop runs so the game status' are updated.
