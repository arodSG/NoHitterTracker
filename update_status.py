#!/usr/bin/python3

# No-Hitter Tracker
#   This script gets the nohittertracker service status (active or inactive) and updates the location of the @NoHitterTracker Twitter account with the status.

import subprocess
from twython import Twython, TwythonError
from auth import (CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

if __name__ == '__main__':
    command = 'systemctl is-active nohittertracker.service'
    process = subprocess.Popen(command.split(), stdout=subprocess.PIPE)
    output, error = process.communicate()
    
    status = output.strip().decode('UTF-8')
    location_text = 'Status: ' + status if status in ['active', 'inactive'] else '?'
    
    try:
        twitter = Twython(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
        twitter.update_profile(location=location_text)
        print('Status successfully updated.')
    except TwythonError as e:
        print('An error occurred and the status was not updated: ' + str(e))