import eventlet
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
import math
from optparse import OptionParser
from tempfile import gettempdir

import _pandora




THIS_DIR = dirname(abspath(__file__))
TEMPLATE_DIR = join(THIS_DIR, "templates")
                        
    





class Connection(object):
    """
    Handles all the direct communication to Pandora's servers
    """
    _pandora_protocol_version = 29
    _pandora_host = "www.pandora.com"
    _pandora_port = 80
    _pandora_rpc_path = "/radio/xmlrpc/v%d" % _pandora_protocol_version

    def __init__(self):
        self.rid = "%07dP" % (time.time() % 10000000) # route id
        self.timeoffset = time.time()
        self.token = None
        self.lid = None # listener id
        
    @staticmethod
    def dump_xml(x):
        """ a convenience function for dumping xml from Pandora's servers """
        #el = xml.dom.minidom.parseString(cElementTree.tostring(x))
        el = xml.dom.minidom.parseString(x)
        return el.toprettyxml(indent="  ")
        
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
        
        logging.debug("talking to pandora %s" % url)
        #logging.debug("sending data %s" % self.dump_xml(body))

        body = _pandora.encrypt(body)
        conn.request("POST", url, body, headers)
        resp = conn.getresponse()

        if resp.status != 200: raise Exception(resp.reason)

        ret_data = resp.read()
        #logging.debug("returned data %s" % self.dump_xml(ret_data))

        conn.close()

        xml = cElementTree.fromstring(ret_data)
        return xml


    def get_template(self, tmpl, params={}):
        """ returns template from the template directory and populates it with
        the params dict.  this saves a lot of work having to manually build
        an xml template """
        tmpl_file = join(TEMPLATE_DIR, tmpl) + ".xml"
        h = open(tmpl_file, "r")
        xml = Template(h.read())
        h.close()

        return xml.substitute(params).strip()


    def sync(self):
        """ synchronizes the times between our clock and pandora's servers by
        recording the timeoffset value, so that for every call made to Pandora,
        we can specify the correct time of their servers in our call """
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
        """ logs us into Pandora.  tries a few times, then fails if it doesn't
        get a listener id """
        logging.info("authenticating with %s" % email)
        get = {"method": "authenticateListener"}
        
        authenticated = False
        authenticate_tries = 3
        
        while not authenticated and authenticate_tries:
            logging.info("trying to authenticate with pandora...")
            body = self.get_template("authenticate", {
                "timestamp": int(time.time() - self.timeoffset),
                "email": email,
                "password": password
            })
            # we use a copy because do some del operations on the dictionary
            # from within send
            xml = self.send(get.copy(), body)
            for el in xml.findall("params/param/value/struct/member"):
                children = el.getchildren()
                if children[0].text == "authToken":
                    self.token = children[1].text
                elif children[0].text == "listenerId":
                    self.lid = children[1].text	
                    
            if self.lid: authenticated = True
            else: 
                authenticate_tries -= 1
                logging.error("failed authentication, trying %d more times" % authenticate_tries)

        if not authenticated:
            logging.error("can't authenticiate with pandora?!")
            raise Exception, "can't authenticate with pandora!?"
        
        logging.info("authenticated with pandora")


        
        
class Account(object):
    def __init__(self, email, password, mp3_cache_dir=None):
        self.connection = Connection()        
        self.email = email
        self.password = password
        self._stations = {}
        self._message_subscribers = {}
        
        if not mp3_cache_dir: mp3_cache_dir = gettempdir()
        self.cache_dir = mp3_cache_dir
        
        self.current_station = None
        self.current_song = None

        self.login()
        
    def stop(self):
        self.current_station.stop()
        
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
        """ a private getter that puts the stations, sorted alphabetically,
        into the self.stations attribute dictionary """
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

        for el in xml.findall("params/param/value/array/data/value"):
            for member in el.findall("struct/member"):
                c = member.getchildren()
                station_params[c[0].text] = c[1].text
                
            station = Station(self, **station_params)
            self._stations[station.id] = station
            
        #self._stations.sort(key=lambda s: s.name)
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

    def play(self, block=False, **kwargs):
        """ plays the next song in the station playlist """
        if self.account.current_station and self.account.current_station is not self:
            self.account.current_station.stop()
            
        logging.info("playing station %s" % self.name)
        self.current_song = self.playlist.popleft()
        self.account.current_song = self.current_song
        self.account.current_station = self
        self.current_song.play(block, **kwargs)
        return self.current_song

    def pause(self):
        self.current_song.pause()
        
    def stop(self):
        self.current_song.stop()
        
    def like(self): self.current_song.like()
    
    def dislike(self):
        self.current_song.dislike()
        self.next()

    def next(self):
        self.publish_message("changing song...")
        _pandora.stop()
        self.play()

    def _get_playlist(self):
        """ a playlist getter.  each call to Pandora's station api returns maybe
        3 songs in the playlist.  so each time we access the playlist, we need
        to see if it's empty.  if it's not, return it, if it is, get more
        songs for the station playlist """
        
        if self._playlist: return self._playlist

        format = "mp3"
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

        #self._playlist[0].load()
        return self._playlist
    playlist = property(_get_playlist)


    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)

    def __str__(self):
        return "%s" % self.name



class Song(object):
    _download_lock = eventlet.semaphore.Semaphore()
    _play_lock = eventlet.semaphore.Semaphore()

    def __init__(self, station, songTitle, artistSummary, audioURL, fileGain, userSeed, musicId, albumTitle, **kwargs):
        self.station = station
        self.seed = userSeed
        self.id = musicId
        self.title = songTitle
        self.album = albumTitle
        self.artist = artistSummary
        
        self.__dict__.update(kwargs)
        
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
        
        self.filename = join(self.station.account.cache_dir, "%s-%s.mp3" % (format_title(songTitle), format_title(artistSummary)))

        self.started = None
        self.stopped = None
        self.paused = False
        
        self.done = False # if the song has finished playing
        self.progress = 0 # number of seconds that have passed in playing
        
        self._stop_playing = eventlet.queue.Queue(1)

    @staticmethod
    def _decrypt_url(url):
        """ decrypts the song url where the song stream can be downloaded.  the
        last 48 bytes are encrypted, so we pass those bytes to the c extension
        and then tack the decrypted value back onto the url """
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
        """ downloads the song file from Pandora's servers, returning the
        filename when complete.  if the file already exists in the cache
        directory, just return that """
        
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

    def play(self, block=False, finished_cb=None):
        """ downloads and plays this song.  if the song is already paused, it
        just resumes.  if block is True, the song will download and play entirely
        before this function returns.  if block is False, the song will download
        and play in a greenthread, so this function will return immediately """
        
        # do we need to just resume?
        if self.paused:
            logging.info("resuming %s" % self)
            _pandora.resume()
            self.paused = False
            return
        
        def load_and_play():
            self._play_lock.acquire()
            
            # stop anything that is currently playing
            _pandora.stop()
        
            self.length = _pandora.play(self._download())            
            logging.info("playing %s" % self)
            self.publish_message("playing %s" % self)
            
            finished_naturally = False
            while True:            
                stats = _pandora.stats()
                if stats:
                    total, pos = stats
                    self.progress = pos
                    if pos == total:
                        self.done = True
                        finished_naturally = True
                        break
                    
                try:
                    self._stop_playing.get(block=True, timeout=.25)
                    logging.debug("got a stop signal for %s, breaking out of play loop" % self)
                    break
                except eventlet.queue.Empty, e: pass

            logging.info("finished playing %s" % self)
            self.publish_message("finished playing %s" % self)
            self._play_lock.release()
            if finished_naturally and callable(finished_cb): eventlet.spawn_n(finished_cb) # call the callback
            
        if block: load_and_play()
        else: eventlet.spawn_n(load_and_play)
        
    def stop(self):
        logging.info("stopping %s" % self)
        _pandora.stop()
        self._stop_playing.put(True)

    def pause(self):
        logging.info("pausing %s" % self)
        _pandora.pause()
        self.paused = True

    def _add_feedback(self, like=True):
        """ common method called by both like and dislike """
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
        logging.info("liking %s" % self)
        self.publish_message("liking %s" % self)
        self._add_feedback(like=True)

    def dislike(self):
        _pandora.stop()
        logging.info("disliking %s" % self)
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
    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-u', "--username", dest="user", help="your Pandora username (your email)")
    parser.add_option('-p', '--password', dest='password', help='your Pandora password')
    parser.add_option('-d', '--debug', dest="logging_debug", action="store_true", default=False, help="turn on debugging")
    (options, args) = parser.parse_args()
    
    # set up logging
    logging_level = logging.INFO
    if options.logging_debug: logging_level = logging.DEBUG
    logging.basicConfig(level=logging_level)
    
    if not options.password or not options.user:
        parser.error("Please provide your username and password")
        
    account = Account(options.user, options.password)

    # play a random station
    from random import randint
    num_stations = len(account.stations)
    random_station = randint(0, num_stations - 1)    
    account.stations[random_station].play(True)