import eventlet
eventlet.monkey_patch()

import time
from xml.etree import cElementTree
import xml.dom.minidom
from string import Template
import httplib
import urllib
from os.path import join, abspath, dirname, exists
import re
from urlparse import urlsplit
from collections import deque
import socket
import logging
import unicodedata
import math


import _pandora

THIS_DIR = dirname(abspath(__file__))
TEMPLATE_DIR = join(THIS_DIR, "templates")
SONG_DIR = join(THIS_DIR, "cache")










                        
    


def dump_xml(x):
    el = xml.dom.minidom.parseString(cElementTree.tostring(x))
    return el.toprettyxml(indent="  ")






class Connection(object):
    _pandora_protocol_version = 29
    _pandora_host = "www.pandora.com"
    _pandora_port = 80
    _pandora_rpc_path = "/radio/xmlrpc/v%d" % _pandora_protocol_version

    def __init__(self):
        # route id
        self.rid = "%07dP" % (time.time() % 10000000)
        self.timeoffset = time.time()
        self.token = None


    def send(self, get_data, body=None):        
        conn = httplib.HTTPConnection("%s:%d" % (self._pandora_host, self._pandora_port))

        headers = {"Content-Type": "text/xml"}

        # pandora has a very specific way that the get params have to be ordered
        # otherwise we'll get a 500 error.  so this orders them correctly.
        ordered = []
        ordered.append(("rid", self.rid))
        
        if "lid" in get_data:
            ordered.append(("lid", get_data["lid"]))
            del get_data["lid"]
            
        ordered.append(("method", get_data["method"]))
        del get_data["method"]
        
        def sort_fn(item):
            k, v = item
            m = re.search("\d+$", k)
            if not m: return k
            else: return int(m.group(0))
        
        kv = [(k, v) for k,v in get_data.iteritems()]
        kv.sort(key=sort_fn)
        ordered.extend(kv)
        
        
        url = "%s?%s" % (self._pandora_rpc_path, urllib.urlencode(ordered))
        
        logging.info("talking to pandora %s" % url)

        #print url
        #print body

        body = _pandora.encrypt(body)
        conn.request("POST", url, body, headers)
        resp = conn.getresponse()

        if resp.status != 200: raise Exception(resp.reason)

        ret_data = resp.read()
        #print ret_data
        #print "\n"
        conn.close()

        xml = cElementTree.fromstring(ret_data)
        return xml


    def get_template(self, tmpl, params={}):
        tmpl_file = join(TEMPLATE_DIR, tmpl) + ".xml"
        h = open(tmpl_file, "r")
        xml = Template(h.read())
        h.close()

        return xml.substitute(params).strip()


    def sync(self):
        get = {"method": "sync"}
        body = self.get_template("sync")
        timestamp = None

        while timestamp is None:
            xml = self.send(get.copy(), body)
            timestamp = xml.find("params/param/value").text
            timestamp = _pandora.decrypt(timestamp)
            
            timestamp_chars = []
            for c in timestamp:
                if c.isdigit(): timestamp_chars.append(c)
            timestamp = int(time.time() - int("".join(timestamp_chars)))

        self.timeoffset = timestamp	    
        return True


    def authenticate(self, email, password):
        logging.info("authenticating with %s" % email)
        get = {"method": "authenticateListener"}
        body = self.get_template("authenticate", {
            "timestamp": int(time.time() - self.timeoffset),
            "email": email,
            "password": password
        })
        xml = self.send(get, body)
        for el in xml.findall("params/param/value/struct/member"):
            children = el.getchildren()
            if children[0].text == "authToken":
                self.token = children[1].text
            elif children[0].text == "listenerId":
                self.lid = children[1].text	



class Account(object):
    def __init__(self, email, password):
        self.connection = Connection()        
        self.email = email
        self.password = password
        self.cookie = None
        self._stations = {}
        self._message_subscribers = {}
        
        self.current_station = None
        self.current_song = None

        self.login()
        
    def publish_message(self, msg):
        for name, subscriber in self._message_subscribers.iteritems():
            subscriber(msg)
    
    def subscribe_to_messages(self, name, subscriber):
        self._message_subscribers[name] = subscriber

    def login(self):
        self.connection.sync()
        self.connection.authenticate(self.email, self.password)

    def logout(self):
        pass

    def _get_stations(self):
        if self._stations: return self._stations
        
        get = {"method": "getStations", "lid": self.connection.lid}
        body = self.connection.get_template("get_stations", {
            "timestamp": int(time.time() - self.connection.timeoffset),
            "token": self.connection.token
        })
        xml = self.connection.send(get, body)

        station_params = {}
        self._stations = {}
        Station._current_id = 0

        stations = []
        for el in xml.findall("params/param/value/array/data/value"):
            for member in el.findall("struct/member"):
                c = member.getchildren()
                station_params[c[0].text] = c[1].text
                
            station = Station(self, **station_params)
            stations.append(station)
            
        stations.sort(key=lambda s: s.name)
        self._stations = dict(enumerate(stations))

        return self._stations
    stations = property(_get_stations)


class Station(object):    
    def __init__(self, account, stationId, stationIdToken, stationName, **kwargs):
        self.account = account
        self.id = stationId
        self.token = stationIdToken
        self.name = stationName
        self.current_song = None
        self._loaded = eventlet.event.Event()
        self._playlist = deque()
        
    def publish_message(self, msg):
        self.account.publish_message(msg)

    def play(self, block=False):
        self.current_song = self.playlist.popleft()
        self.account.current_song = self.current_song
        self.account.current_station = self
        self.current_song.play(block)
        
    def preload_next(self, block=True):
        self.playlist[0].load(block=block)

    def pause(self):
        self.current_song.pause()
        
    def like(self): self.current_song.like()
    
    def dislike(self):
        self.current_song.dislike()
        self.next()

    def next(self):
        #if self.account._queued_song: self.account._queued_song.cancel()
        self.publish_message("changing song...")
        _pandora.stop()
        self.play()

    def _get_playlist(self):
        if self._playlist: return self._playlist

        format = "mp3-hifi"
        get = {
            "method": "getFragment", "lid": self.account.connection.lid,
            "arg1": self.id, "arg2": 0, "arg3": "", "arg4": "", "arg5": format,
            "arg6": 0, "arg7": 0
        }
        body = self.account.connection.get_template("get_playlist", {
            "timestamp": int(time.time() - self.account.connection.timeoffset),
            "token": self.account.connection.token,
            "station_id": self.id,
            "format": format
        })
        xml = self.account.connection.send(get, body)

        song_params = {}
        self._playlist = deque()

        for el in xml.findall("params/param/value/array/data/value"):
            for member in el.findall("struct/member"):
                c = member.getchildren()
                song_params[c[0].text] = c[1].text
            song = Song(self, **song_params)
            self._playlist.append(song)

        print song_params
        # only download the first one
        #self._playlist[0].load()
        return self._playlist
    playlist = property(_get_playlist)


    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)

    def __str__(self):
        return "%s" % self.name



class Song(object):
    _download_lock = eventlet.semaphore.Semaphore()

    def __init__(self, station, songTitle, artistSummary, audioURL, fileGain, userSeed, musicId, **kwargs):
        self.station = station
        self.seed = userSeed
        self.id = musicId
        self.title = songTitle
        self.artist = artistSummary
        try: self.gain = float(fileGain)
        except: self.gain = 0.0
        self.url = self._decrypt_url(audioURL)
        self.length = 0 # will be populated when played
        
        def format_title(part):
            part = part.lower()
            part = part.replace(" ", "_")
            part = re.sub("\W", "", part)
            part = re.sub("_+", "_", part)
            return part
        
        self.filename = join(SONG_DIR, "%s-%s.mp3" % (format_title(songTitle), format_title(artistSummary)))
        
        self.offset_events = None

        self.started = None
        self.stopped = None
        self.paused = False

        self._db_key = ""
        self.stats = {
            "plays": 0
        }

    @staticmethod
    def _decrypt_url(url):
        e = url[-48:]
        d = _pandora.decrypt(e)
        url = url.replace(e, d)
        return url[:-8]

    def load(self, block=False):
        if block: self._download()
        else: eventlet.spawn_n(self._download)
        
    def publish_message(self, msg):
        self.station.account.publish_message(msg)

    def _download(self):
        self._download_lock.acquire()
                
        # dont re-download if it already exists in cache
        if exists(self.filename):
            logging.info("found existing file for %s" % self.filename)
            self._download_lock.release()
            return self.filename
        logging.info("downloading %s" % self.filename)
        
        split = urlsplit(self.url)
        host = split.netloc
        path = split.path + "?" + split.query

        c = httplib.HTTPConnection(host)
        c.request("GET", path, headers={"Range": "bytes=%d-" % 0})
        res = c.getresponse()

        h = open(self.filename, "w")
        h.write(res.read())
        c.close()
        h.close()

        self._download_lock.release()
        return self.filename

    def new_station(self, station_name):
        """ create a new station from this song """
        raise NotImplementedError

    def play(self, block=False):
        if self.paused:
            _pandora.resume()
            self.paused = False
            return
        
        def load_and_play():
            self.length = _pandora.play(self._download())
            self.publish_message("playing %s" % self)
            
            while True:            
                stats = _pandora.stats()
                if stats:
                    total, pos = stats
                    if pos == total and account.current_station: break
                        
                time.sleep(1)
            self.publish_message("finished playing %s" % self)
            
        if block: load_and_play()
        else: eventlet.spawn_n(load_and_play)
        
    def stop(self):
        _pandora.stop()

    def pause(self):
        _pandora.pause()
        self.paused = True

    def _add_feedback(self, like=True):
        conn = self.station.account.connection
        
        get = {
            "method": "addFeedback",
            "lid":  conn.lid,
            "arg1": self.station.id,
            "arg2": self.id,
            "arg3": self.seed,
            "arg4": 0, "arg5": str(like).lower(), "arg6": "false", "arg7": 1
        }
        body = conn.get_template("add_feedback", {
            "timestamp": int(time.time() - conn.timeoffset),
            "station_id": self.station.id,
            "token": conn.token,
            "music_id": self.id,
            "seed": self.seed,
            "arg4": 0, "arg5": int(like), "arg6": 0, "arg7": 1
        })
        xml = conn.send(get, body)
        
    def like(self):
        self.publish_message("liking %s" % self)
        self._add_feedback(like=True)

    def dislike(self):
        _pandora.stop()
        self.publish_message("disliking %s" % self)
        self._add_feedback(like=False)
        self.station.next()

    def __str__(self):
        minutes = int(math.floor(float(self.length) / 60))
        seconds = int(self.length - (minutes * 60))
        return "\"%s\" by %s (%d:%02d)" % (self.title, self.artist, minutes, seconds)

    def __repr__(self):
        return "<Song \"%s\" by \"%s\">" % (self.title, self.artist)




if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    account = Account("", "")
    account.stations[0].play(True)