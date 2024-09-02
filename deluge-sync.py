#!/usr/bin/python3

import requests
import json
import time
import sys
import re
import getopt
import configparser

parser = configparser.ConfigParser()
parser.read('deluge.ini')
config = parser['main']

password = config['password']
url = config['url']
remote_path = config['remote_path']

output = True
remove_list = []
headers = {'Content-type': 'application/json', 'Accept': 'text/plain'}

def Output(msg):
    global output
    if output:
        print(msg)

def logError(msg):
    Output("Error: "+msg)
    sys.exit(1)

def get_login_cookie():
    data = {"method":"auth.login","params":[password],"id":'13'}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    j = r.cookies.get_dict()

    if '_session_id' in j:
        returnmsg = j['_session_id']
        Output( "Logging In" )
    else:
        logError("Bad Password!")
    return returnmsg

def remove_torrent(id):
    data = {"method":"core.remove_torrent","params":[id,"true"],"id":"2030"}
    r = ""
    Output( "\t\tAttempting to remove " + id )
    try:
        r = requests.post(url, data=json.dumps(data), headers=headers, timeout=5)
        r.raise_for_status()
        Output( "\t\tRemoved torrent " + id )
        remove_list.remove( id )
    except requests.exceptions.RequestException as error:
        Output('\t\t *** Failed to remove Item ***')
        Output(error)
    Output("")
    return r

def move_torrent(id):
    data = {"method":"core.move_storage","params":[[id],remote_path],"id":"112"}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    return r

def change_label_torrent(id,label):
    data = {"method":"label.set_torrent","params":[id,label],"id":"9641"}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    return r

# I have no idea what I was doing here, but it's on my version in GitHub...
#def sync_torrent(torrent):
#    id = torrent[0]
#    name = torrent[1]
#    label   = torrent[2]
#    path = remote_user+"@"+remote_host+":\""+remote_path+label+"/"+name+"\""
#
#    print path
#    proc = subprocess.Popen(["rsync", "--exclude=\".*\"", "-PrtDhv","--perms", "--chmod", "755", path, local_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
#    pout,perr = proc.communicate()
#    if not perr:
#        print pout
#        change_label_torrent(id,'seeding')
#        move_torrent(id)
#
#    r = ""
#    return r

def get_torrents():
    Output( "\tGetting List of Torrents" )
    data = {"method":"web.update_ui","params":[["name","total_wanted","state","time_added","tracker_host","seeding_time","label"],{"state":"Seeding"}],"id":22}
    r = requests.post(url, data=json.dumps(data), headers=headers)
    j = r.json()
    result = j['result']
    if result is not None:
        if result['connected'] is not False:
            returnmsg = result['torrents']
            Output( "\tTorrent List Gathered" )
        else:
            logError( "Not Connected" )
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

def usage():
    Output( '-h / --help Displays this page' )
    Output( '-q / --quiet will surpress output' )

def main():
    try:
        opts, args = getopt.getopt(sys.argv[1:], "h:q", ["help", "quiet="])
    except getopt.GetoptError as err:
        # print help information and exit:
        print(err)  # will print something like "option -a not recognized"
        usage()
        sys.exit(2)

    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit()
        elif o in ("-q", "--quiet"):
            output = False
        else:
            assert False, "unhandled option"



    headers['Cookie'] = '_session_id='+get_login_cookie()

    torrents = get_torrents()

    for tid, torrent in torrents.items():
        name = torrent['name']
        state = torrent['state']
        tracker_host = torrent['tracker_host']
        time_added = torrent['time_added']
        total_wanted = torrent['total_wanted']
        seeding_time = torrent['seeding_time']

        if (tracker_host == "landof.tv"):
    #        if seeding_time > 2678400:
            regexp = re.compile(r'(?i)S[0-9][0-9]E[0-9][0-9]')
            if regexp.search(name):
                if seeding_time > 93600:
                    remove_list.append( tid )
            if seeding_time > 475200:
    #        if seeding_time > 1123200:
                remove_list.append( tid )

        if (tracker_host == "torrentbytes.net"):
            if seeding_time > 259200:
                remove_list.append( tid )

        if (tracker_host != "landof.tv") and (tracker_host != "torrentbytes.net"):
            if seeding_time > 5400:
                remove_list.append( tid )

    if len(remove_list) > 0:
        for torrentid in reversed(remove_list):
            r = remove_torrent(torrentid)
            time.sleep(1)

if __name__ == "__main__":
    main()
