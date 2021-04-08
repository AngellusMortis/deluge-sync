#!/usr/bin/python

import requests
import json
import time
import sys
import subprocess
import configparser

parser = configparser.ConfigParser()
parser.read('deluge.ini')
config = parser['main']

password = config['password']
url = config['url']
remote_path = config['remote_path']
remote_user = config['remote_user']
remote_host = config['remote_host']
local_path = config['local_path']
headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}

def logError(msg):
    print("Error: "+msg)
    sys.exit(1)

def get_login_cookie():
    data = {"method":"auth.login","params":[password],"id":'13'}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    j = r.cookies.get_dict()

    if '_session_id' in j:
        returnmsg = j['_session_id']
    else:
        logError("Bad Password!")
    return returnmsg

def remove_torrent(id):
    data = {"method":"core.remove_torrent","params":[id,"true"],"id":"2030"}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    return r

def move_torrent(id):
    data = {"method":"core.move_storage","params":[[id],remote_path+"Seeding/"],"id":"112"}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    return r

def change_label_torrent(id,label):
    data = {"method":"label.set_torrent","params":[id,label],"id":"9641"}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    return r

def sync_torrent(torrent):
    id = torrent[0]
    name = torrent[1]
    label   = torrent[2]
    path = remote_user+"@"+remote_host+":\""+remote_path+label+"/"+name+"\""

    print path
    proc = subprocess.Popen(["rsync", "--exclude=\".*\"", "-PrtDhv","--perms", "--chmod", "755", path, local_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    pout,perr = proc.communicate()
    if not perr:
        print pout
        change_label_torrent(id,'seeding')
        move_torrent(id)

    r = ""
    return r

def get_torrents():
    data = {"method":"web.update_ui","params":[["name","total_wanted","state","time_added","tracker_host","seeding_time","label"],{"state":"Seeding","label":"radarr-new"}],"id":22}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    j = r.json()
    result = j['result']
    if result is not None:
        returnmsg = result['torrents']
    else:
        if 'error' in j:
            logError(j['error']['message'])
    return returnmsg

def get_torrents_Error():
    data = {"method":"web.update_ui","params":[["name","total_wanted","state","time_added","tracker_host","seeding_time"],{"state":"Error"}],"id":22}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    j = r.json()
    result = j['result']
    if result is not None:
        returnmsg = result['torrents']
    else:
        if 'error' in j:
            logError(j['error']['message'])
    return returnmsg


headers['Cookie'] = '_session_id='+get_login_cookie()

sync_list = []

torrents = get_torrents()

for tid, torrent in torrents.items():
    name = torrent['name']
    state = torrent['state']
    tracker_host = torrent['tracker_host']
    time_added = torrent['time_added']
    total_wanted = torrent['total_wanted']
    seeding_time = torrent['seeding_time']
    label = torrent['label']

    if seeding_time > 60:
        sync_list.append( [tid,name,label] )

if len(sync_list) > 0:
    for torrentid in sync_list:
        r = sync_torrent(torrentid)
