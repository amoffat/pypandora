#===============================================================================
# Copyright (C) 2011 by Andrew Moffat
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#===============================================================================

import time
from xml.etree import ElementTree
import xml.dom.minidom
from xml.sax.saxutils import escape as xml_escape
from string import Template
import httplib
import urllib
from os.path import join, abspath, dirname, exists
import re
from urlparse import urlsplit
import socket
import logging
import math
from optparse import OptionParser
from tempfile import gettempdir
import struct
from ctypes import c_uint32
from pprint import pprint
import select
import errno
import sys
try: import simplejson as json
except ImportError: import json
from Queue import Queue
from base64 import b64decode, b64encode
import zlib
from random import choice
from webbrowser import open as webopen

try: from urlparse import parse_qsl, parse_qs
except ImportError: from cgi import parse_qsl, parse_qs







THIS_DIR = dirname(abspath(__file__))
music_buffer_size = 20
import_export_html_filename = "index.html"



# settings
settings = {
    'username': None,
    'download_directory': '/tmp',
    'download_music': False,
    'volume': 60,
    'tag_mp3s': True,
    'last_station': None,
    'password': None,
}




def save_setting(**kwargs):
    """ saves a value persisitently *in the file itself* so that it can be
    used next time pypandora is fired up.  of course there are better ways
    of storing values persistently, but i want to stick with the '1 file'
    idea """
    global settings
    
    logging.info("saving values %r", kwargs)
    with open(abspath(__file__), "r") as h: lines = h.read()
    
    
    start = lines.index("settings = {\n")
    end = lines[start:].index("}\n") + start + 2
    
    chunks = [lines[:start], "", lines[end:]]
    
    settings.update(kwargs)
    new_settings = "settings = {\n"
    for k,v in settings.iteritems(): new_settings += "    %r: %r,\n" % (k, v)
    new_settings += "}\n"
    
    chunks[1] = new_settings
    new_contents = "".join(chunks)
    
    with open(abspath(__file__), "w") as h: h.write(new_contents)





class LoginFail(Exception): pass


class Connection(object):
    """
    Handles all the direct communication to Pandora's servers
    """
    _pandora_protocol_version = 32
    _pandora_host = "www.pandora.com"
    _pandora_port = 80
    _pandora_rpc_path = "/radio/xmlrpc/v%d" % _pandora_protocol_version
    
    _templates = {
        "sync": """
eNqzsa/IzVEoSy0qzszPs1Uy1DNQsrezyU0tychPcU7MyYGx/RJzU+1yM4uT9Yor85Jt9JFEbQoSixJz
i+1s9OEMJP0Afngihg==""",
        "add_feedback": """
eNqdkssKwjAQRX9FSteNgo/NmC4El/6CTJuhhuZRkrT4+caSQl2IravMzdwzhLmB8qnVZiDnpTXnbFds
s5KDpvCw4oJKTfUNNXEfMERbgUJciUSFdQts1ocOHWqfTg4Dqj7eShN4HqSmyOsO2FsDS02WvJ+ID06a
JlK2JQMsyYVQeuZdirWk7r2s/+B83MZCJkfX7H+MraxVhGb0HoBNcgV1XEyN4UTi9CUXNmXKZp/iBbQI
yo4=""",
        "authenticate": """
eNqNj8EKwkAMRH9FSs+N3uP24FX8h2CDDWx2yyZt/XwVtlAPgqdkJvNggv1T42HhYpLTuTl1x6YPqOxj
Hi4U47bfSDlEMefEpaPZR04ud3K+VhNhl8SJCqnVGXChOL9dSR5aF2Vz0gnhoxHqEWr2GzEvkh6hZSWJ
CFX+CU1ktuYy/OZgKwq7n1/FhWTE""",
        "get_playlist": """
eNq1ks8KwjAMxl9Fxs7LvMfuIHj0FSSwOItNO9o49O2t0MG8iDvslH+/j/CRYPcUt5s4Jhv8odo3bdUZ
FNZb6I/k3JyfSdiMjl7OJm0G1lOkQdgrwgLAkSJJKtHgRO6Ru9arqdUKJyUZET41QhlCYb8lSaP1Q1aF
O3uEUv4pyms027nYfqWyXclvi9fXEIV0Yw8/eJjPCYuHeAObkcrC""",
        "get_stations": """
eNpljrEOgzAMRH8FIWbc7iYM3bv0CyzVolHjBMUu4vNJ1SCBOvnOd082jquEZuGsPsWhvfaXdnQobK/0
vFEIu76TsFMjK7V+Ynv8pCIccpwpk2idDhcKn7L10VxnXrjwMiN8PUINoXbPiFr2cSpUenNEqPYPgv0g
HD7eAIijTD8="""
    }

    def __init__(self):
        self.rid = "%07dP" % (time.time() % 10000000) # route id
        self.timeoffset = time.time()
        self.token = None
        self.lid = None # listener id
        self.log = logging.getLogger("pandora")

    @staticmethod
    def dump_xml(x):
        """ a convenience function for dumping xml from Pandora's servers """
        #el = xml.dom.minidom.parseString(ElementTree.tostring(x))
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

        self.log.debug("talking to %s", url)

        # debug logging?
        self.log.debug("sending data %s" % self.dump_xml(body))

        body = encrypt(body)
        conn.request("POST", url, body, headers)
        resp = conn.getresponse()

        if resp.status != 200: raise Exception(resp.reason)

        ret_data = resp.read()

        # debug logging?
        self.log.debug("returned data %s" % self.dump_xml(ret_data))

        conn.close()

        xml = ElementTree.fromstring(ret_data)
        return xml


    def get_template(self, tmpl, params={}):
        tmpl = zlib.decompress(b64decode(self._templates[tmpl].strip().replace("\n", "")))        
        xml = Template(tmpl)
        return xml.substitute(params).strip()


    def sync(self):
        """ synchronizes the times between our clock and pandora's servers by
        recording the timeoffset value, so that for every call made to Pandora,
        we can specify the correct time of their servers in our call """
        
        self.log.info("syncing time")
        get = {"method": "sync"}
        body = self.get_template("sync")
        timestamp = None


        while timestamp is None:
            xml = self.send(get.copy(), body)
            timestamp = xml.find("params/param/value").text
            timestamp = decrypt(timestamp)

            timestamp_chars = []
            for c in timestamp:
                if c.isdigit(): timestamp_chars.append(c)
            timestamp = int(time.time() - int("".join(timestamp_chars)))

        self.timeoffset = timestamp	    
        return True


    def authenticate(self, email, password):
        """ logs us into Pandora.  tries a few times, then fails if it doesn't
        get a listener id """
        self.log.info("logging in with %s...", email)
        get = {"method": "authenticateListener"}


        body = self.get_template("authenticate", {
            "timestamp": int(time.time() - self.timeoffset),
            "email": xml_escape(email),
            "password": xml_escape(password)
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

        if self.lid: return True        
        return False






class Account(object):
    def __init__(self, reactor, email, password):
        self.reactor = reactor
        self.reactor.shared_data["pandora_account"] = self
        
        self.log = logging.getLogger("account %s" % email)
        self.connection = Connection()        
        self.email = email
        self.password = password
        self._stations = {}
        self.recently_played = []

        self.current_station = None
        self.msg_subscribers = []
        
        self.login()
        self.start()
        
        
        def song_changer():
            sd = self.reactor.shared_data
            
            if self.current_song and self.current_song.done_playing:
                self.current_station.next()
                sd["message"] = ["refresh_song"]
                
        self.reactor.add_callback(song_changer)
        
        
    def start(self):
        """ loads the last-played station and kicks it to start """
        # load our previously-saved station
        station_id = settings.get("last_station", None)
        
        # ...or play a random one
        if not station_id or station_id not in self.stations:
            station_id = choice(self.stations.keys())
            save_setting(last_station=station_id)
            
        self.play(station_id)
        
        
    def next(self):
        if self.current_station: self.current_station.next()
        
    def like(self):
        if self.current_station: self.current_station.like()
        
    def dislike(self):
        if self.current_station: self.current_station.dislike()
        
    def play(self, station_id):
        if self.current_station: self.current_station.stop()
        station = self.stations[station_id]
        station.play()
        return station
        
    @property
    def current_song(self):
        return self.current_station.current_song
            
    def login(self):
        logged_in = False
        for i in xrange(3):
            self.connection.sync()
            if self.connection.authenticate(self.email, self.password):
                logged_in = True
                break
            else:
                self.log.error("failed login (this happens quite a bit), trying again...")
                time.sleep(1)
        if not logged_in:
            self.reactor.shared_data["pandora_account"] = None
            raise LoginFail, "can't log in.  wrong username or password?"
        self.log.info("logged in")
        
    @property
    def json_data(self):
        data = {}
        data["stations"] = [(id, station.name) for id,station in self.stations.iteritems()]
        data["stations"].sort(key=lambda s: s[1].lower())
        data["current_station"] = getattr(self.current_station, "id", None)
        data["volume"] = settings["volume"]
        return data
            

    @property
    def stations(self):
        if self._stations: return self._stations
        
        self.log.info("fetching stations")
        get = {"method": "getStations", "lid": self.connection.lid}
        body = self.connection.get_template("get_stations", {
            "timestamp": int(time.time() - self.connection.timeoffset),
            "token": self.connection.token
        })
        xml = self.connection.send(get, body)

        fresh_stations = {}
        station_params = {}
        Station._current_id = 0

        for el in xml.findall("params/param/value/array/data/value"):
            for member in el.findall("struct/member"):
                c = member.getchildren()
                station_params[c[0].text] = c[1].text

            station = Station(self, **station_params)
            fresh_stations[station.id] = station


        # remove any stations that pandora says we don't have anymore
        for id, station in self._stations.items():
            if not fresh_stations.get(id): del self._stations[id]

        # add any new stations if they don't already exist
        for id, station in fresh_stations.iteritems():
            self._stations.setdefault(id, station)

        self.log.info("got %d stations", len(self._stations))
        return self._stations




class Station(object):    
    PLAYLIST_LENGTH = 3

    def __init__(self, account, stationId, stationIdToken, stationName, **kwargs):
        self.account = account
        self.id = stationId
        self.token = stationIdToken
        self.name = stationName
        self.current_song = None
        self._playlist = []
        
        self.log = logging.getLogger(str(self).encode("ascii", "ignore"))

    def like(self):
        # normally we might do some logging here, but we let the song object
        # handle it
        self.current_song.like()

    def dislike(self):
        self.current_song.dislike()
        self.next()
        
    def stop(self):
        if self.current_song: self.current_song.stop()
    
    def play(self):
        # next() is an alias to play(), so we check if we're changing the
        # station before we output logging saying such
        if self.account.current_station and self.account.current_station is not self:        
            self.log.info("changing station to %r", self)
            
        self.account.current_station = self
        self.stop()
        
        self.playlist.reverse()
        if self.current_song: self.account.recently_played.append(self.current_song)
        self.current_song = self.playlist.pop()
        
        self.log.info("playing %r", self.current_song)
        self.playlist.reverse()
        self.current_song.play()
            
    def next(self):
        self.account.reactor.shared_data["message"] = ["refresh_song"]
        self.play()
    
    @property
    def playlist(self):
        """ a playlist getter.  each call to Pandora's station api returns maybe
        3 songs in the playlist.  so each time we access the playlist, we need
        to see if it's empty.  if it's not, return it, if it is, get more
        songs for the station playlist """

        if len(self._playlist) >= Station.PLAYLIST_LENGTH: return self._playlist

        self.log.info("getting playlist")
        format = "mp3-hifi" # always try to select highest quality sound
        get = {
            "method": "getFragment", "lid": self.account.connection.lid,
            "arg1": self.id, "arg2": 0, "arg3": "", "arg4": "", "arg5": format,
            "arg6": 0, "arg7": 0
        }

        got_playlist = False
        for i in xrange(2):
            body = self.account.connection.get_template("get_playlist", {
                "timestamp": int(time.time() - self.account.connection.timeoffset),
                "token": self.account.connection.token,
                "station_id": self.id,
                "format": format
            })
            xml = self.account.connection.send(get, body)

            song_params = {}

            for el in xml.findall("params/param/value/array/data/value"):
                for member in el.findall("struct/member"):
                    key = member[0].text
                    value = member[1]
                    
                    number = value.find("int")
                    if number is not None: value = int(number.text)
                    else: value = value.text
                     
                    song_params[key] = value
                song = Song(self, **song_params)
                self._playlist.append(song)

            if self._playlist:
                got_playlist = True
                break
            else:
                self.log.error("failed to get playlist, trying again times")
                self.account.login()

        if not got_playlist: raise Exception, "can't get playlist!"
        return self._playlist

    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)




class Song(object):
    assume_bitrate = 128
    read_chunk_size = 1024
    kb_to_quick_stream = 256
    
    # states
    INITIALIZED = 0
    SENDING_REQUEST = 1
    READING_HEADERS = 2
    STREAMING = 3
    DONE = 4
    

    def __init__(self, station, **kwargs):
        self.station = station
        self.reactor = self.station.account.reactor

        self.__dict__.update(kwargs)
        #pprint(self.__dict__)
        
        self.seed = self.userSeed
        self.id = self.musicId
        self.title = self.songTitle
        self.album = self.albumTitle
        self.artist = self.artistSummary
        
        self.liked = bool(self.rating)
        
        # see if the big version of the album art exists
        if self.artRadio:
            art_url = self.artRadio.replace("130W_130H", "500W_500H")
            art_url_parts = urlsplit(art_url)
            
            test_art = httplib.HTTPConnection(art_url_parts.netloc)
            test_art.request("HEAD", art_url_parts.path)
            if test_art.getresponse().status != 200: art_url = self.artRadio
        else:
            art_url = self.artistArtUrl
        
        self.album_art = art_url


        self.purchase_itunes =  kwargs.get("itunesUrl", "")
        if self.purchase_itunes:
            self.purchase_itunes = urllib.unquote(parse_qsl(self.purchase_itunes)[0][1])

        self.purchase_amazon = kwargs.get("amazonUrl", "")


        try: self.gain = float(fileGain)
        except: self.gain = 0.0

        self.url = self._decrypt_url(self.audioURL)
        self.duration = 0
        self.song_size = 0
        self.download_progress = 0
        self.last_read = 0
        self.state = Song.INITIALIZED
        self.started_streaming = None
        self.sock = None
        self.bitrate = None
        
        
        # these are used to prevent .done_playing from reporting too early in
        # the case where we've closed the browser window (and are therefore not
        # streaming audio out of the buffer)
        self._done_playing_offset = 0
        self._done_playing_marker = 0

        def format_title(part):
            part = part.lower()
            part = part.replace(" ", "_")
            part = re.sub("\W", "", part)
            part = re.sub("_+", "_", part)
            return part

        self.filename = join(settings["download_directory"], "%s-%s.mp3" % (format_title(self.artist), format_title(self.title)))
        
        # FIXME: bug if the song has weird characters
        self.log = logging.getLogger(str(self).encode("ascii", "ignore"))
        
        
        
    @property
    def json_data(self):
        return {
            "id": self.id,
            "album_art": self.album_art,
            "title": self.title,
            "album": self.album,
            "artist": self.artist,
            "purchase_itunes": self.purchase_itunes,
            "purchase_amazon": self.purchase_amazon,
            "gain": self.gain,
            "duration": self.duration,
            "liked": self.liked,
        }
        

    @staticmethod
    def _decrypt_url(url):
        """ decrypts the song url where the song stream can be downloaded. """
        e = url[-48:]
        d = decrypt(e)
        url = url.replace(e, d)
        return url[:-8]
    
    @property
    def position(self):
        if not self.song_size: return 0
        return self.duration * self.download_progress / float(self.song_size)
    
    @property
    def done_playing(self):
        # never finish playing if we're not actually pushing data through out
        # to the audio player
        if self._done_playing_marker: return False
        
        return self.started_streaming and self.duration\
            and self.started_streaming + self.duration + self._done_playing_offset <= time.time()
    
    @property
    def done_downloading(self):
        return self.download_progress and self.download_progress == self.song_size
        
    def fileno(self):
        return self.sock.fileno()
    
    
    def stop(self):
        self.reactor.remove_all(self)
        if self.sock:
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except: pass
            self.sock.close()
        
    
    def play(self):
        self.connect()
        
        # the first thing we do is send out the request for the music, so we
        # need the select reactor to know about us
        self.reactor.add_writer(self)
        
        
    def connect(self):
        # we stop the song just in case we're reconnecting...because we dont
        # want the old socket laying around, open, and in the reactor
        self.stop()
        
        self.log.info("downloading from byte %d", self.download_progress)
        
        split = urlsplit(self.url)
        host = split.netloc
        path = split.path + "?" + split.query
        
        req = """GET %s HTTP/1.0\r\nHost: %s\r\nRange: bytes=%d-\r\nUser-Agent: pypandora\r\nAccept: */*\r\n\r\n"""
        self.sock = MagicSocket(host=host, port=80)
        self.sock.write_string(req % (path, host, self.download_progress))
        self.state = Song.SENDING_REQUEST
        
        # if we're reconnecting, we might be in a state of being in the readers
        # and not the writers, so let's just ensure that we're where we need
        # to be
        self.reactor.remove_reader(self)
        self.reactor.add_writer(self)
        
        
        
    def _calc_bitrate(self, chunk):
        """ takes a chunk of mp3 data, finds the sync frame in the header
        then filters out the bitrate (if it can be found) """
        
        bitrate_lookup = {
            144: 128,
            160: 160,
            176: 192,
            192: 224,
            208: 256,
            224: 320
        }
    
        for i in xrange(0, len(chunk), 2):
            c = chunk[i:i+2]
            c = struct.unpack(">H", c)[0]
            
            if c & 65504:
                bitrate_byte = ord(chunk[i+2])
                try: return bitrate_lookup[bitrate_byte & 240]
                except KeyError: return None
        
        return None
        
        
    def handle_write(self, shared, reactor):
        if self.state is Song.SENDING_REQUEST:
            done = self.sock.write()
            if done:
                self.reactor.remove_writer(self)
                self.reactor.add_reader(self)
                self.state = Song.READING_HEADERS
                self.sock.read_until("\r\n\r\n")
            return
        

    def handle_read(self, shared, reactor):
        if self.state is Song.DONE:
            return
        
        if self.state is Song.READING_HEADERS:
            status, headers = self.sock.read()
            if status is MagicSocket.DONE:
                # parse our headers
                headers = headers.strip().split("\r\n")
                headers = dict([h.split(": ") for h in headers[1:]])
                
                #print headers
                
                # if we don't have a song size it means we're not doing
                # a reconnect, because if we were, we don't need to do
                # anything in this block
                if not self.song_size:
                    # figure out how fast we should download and how long we need to sleep
                    # in between reads.  we have to do this so as to not stream to quickly
                    # from pandora's servers.  we lower it by 20% so we never suffer from
                    # a buffer underrun.
                    #
                    # these values aren't necessarily correct, but we can't know that
                    # until we get some mp3 data, from which we'll calculate the actual
                    # bitrate, then the dependent values.  but for now, using
                    # Song.assume_bitrate is fine.
                    bytes_per_second = Song.assume_bitrate * 125.0
                    self.sleep_amt = Song.read_chunk_size * .8 / bytes_per_second
                    
                    # determine the size of the song, and from that, how long the
                    # song is in seconds
                    self.song_size = int(headers["Content-Length"])
                    self.duration = (self.song_size / bytes_per_second) + 1
                    self.started_streaming = time.time()
                    self._mp3_data = []
                
                self.state = Song.STREAMING
                self.sock.read_amount(self.song_size - self.download_progress)
            return

        elif self.state is Song.STREAMING:            
            # can we even put anything new on the music buffer?
            if shared_data["music_buffer"].full():
                if not self._done_playing_marker:
                    self._done_playing_marker = time.time()
                return
            
            # it's time to aggregate the time that we sat essentially paused
            # and add it to the offset.  the offset is used to adjust the
            # time calculations to determine if we're done playing the song
            if self._done_playing_marker:
                self._done_playing_offset += time.time() - self._done_playing_marker
                self._done_playing_marker = 0
            
            # check if it's time to read more music yet.  preload the
            # first N kilobytes quickly so songs play immediately
            now = time.time()
            if now - self.last_read < self.sleep_amt and\
                self.download_progress > Song.kb_to_quick_stream * 1024: return
            
            self.last_read = now
            try: status, chunk = self.sock.read(Song.read_chunk_size, only_chunks=True)
            except:
                self.log.exception("error downloading chunk")
                self.connect()
                return
            
            if status is MagicSocket.BLOCKING: return
            
                
            if chunk:
                # calculate the actual bitrate from the mp3 stream data
                if not self.bitrate:
                    self.log.debug("looking for bitrate...")
                    self.bitrate = self._calc_bitrate(chunk)
                    
                    # now that we have the actual bitrate, let's recalculate the song
                    # duration and how fast we should download the mp3 stream
                    if self.bitrate:
                        self.log.debug("found bitrate %d", self.bitrate)
                        
                        bytes_per_second = self.bitrate * 125.0
                        self.sleep_amt = Song.read_chunk_size * .8 / bytes_per_second
                        self.duration = (self.song_size / bytes_per_second) + 1
                    
                    
                self.download_progress += len(chunk)
                self._mp3_data.append(chunk)
                shared_data["music_buffer"].put(chunk)
                
            # disconnected?  do we need to reconnect, or have we read everything
            # and the song is done?
            else:
                if not self.done_downloading:
                    self.log.error("disconnected, reconnecting at byte %d of %d", self.download_progress, self.song_size)
                    self.connect()
                    return
                
                # done!
                else:
                    self.status = Song.DONE
                    self.reactor.remove_all(self)
                    
                    if settings["download_music"]:
                        self.log.info("saving file to %s", self.filename)
                        mp3_data = "".join(self._mp3_data)
                        
                        # save on memory
                        self._mp3_data = []
                        
                        if settings["tag_mp3s"]:
                            # tag the mp3
                            tag = ID3Tag()
                            tag.add_id(self.id)
                            tag.add_title(self.title)
                            tag.add_album(self.album)
                            tag.add_artist(self.artist)
                            # can't get this working...
                            #tag.add_image(self.album_art)
                            
                            mp3_data = tag.binary() + mp3_data
                
                        # and write it to the file
                        h = open(self.filename, "w")
                        h.write(mp3_data)
                        h.close()
                    
                
                

        
        

    def new_station(self, station_name):
        """ create a new station from this song """
        raise NotImplementedError

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
        self.log.info("liking")
        self.liked = True
        self._add_feedback(like=True)

    def dislike(self):
        self.log.info("disliking")
        self.liked = False
        self._add_feedback(like=False)

    def __repr__(self):
        return "<Song \"%s\" by \"%s\">" % (self.title, self.artist)





class ID3Tag(object):
    def __init__(self):
        self.frames = []

    def add_frame(self, name, data):
        name = name.upper()
        # null byte means latin-1 encoding...
        # see section 4 http://www.id3.org/id3v2.4.0-structure
        header = struct.pack(">4siBB", name, self.sync_encode(len(data)), 0, 0)
        self.frames.append(header + data)

    def add_artist(self, artist):
        self.add_frame("tpe1", "\x00" + artist)

    def add_title(self, title):
        self.add_frame("tit2", "\x00" + title)

    def add_album(self, album):
        self.add_frame("talb", "\x00" + album)

    def add_id(self, id):
        self.add_frame("ufid", "\x00" + id)

    def add_image(self, image_url):
        mime_type = "\x00" + "-->" + "\x00"
        description = "cover image" + "\x00"
        # 3 for cover image
        data = struct.pack(">B5sB12s", 0, mime_type, 3, description)
        data += image_url
        self.add_frame("apic", data)

    def binary(self):
        total_size = sum([len(frame) for frame in self.frames])
        header = struct.pack(">3s2BBi", "ID3", 4, 0, 0, self.sync_encode(total_size))
        return header + "".join(self.frames)

    def add_to_file(self, f):
        h = open(f, "r+b")
        mp3_data = h.read()
        h.truncate(0)
        h.seek(0)
        h.write(self.binary() + mp3_data)
        h.close()

    def sync_decode(self, x):
        x_final = 0x00;
        a = x & 0xff;
        b = (x >> 8) & 0xff;
        c = (x >> 16) & 0xff;
        d = (x >> 24) & 0xff;

        x_final = x_final | a;
        x_final = x_final | (b << 7);
        x_final = x_final | (c << 14);
        x_final = x_final | (d << 21);
        return x_final

    def sync_encode(self, x):
        x_final = 0x00;
        a = x & 0x7f;
        b = (x >> 7) & 0x7f;
        c = (x >> 14) & 0x7f;
        d = (x >> 21) & 0x7f;

        x_final = x_final | a;
        x_final = x_final | (b << 8);
        x_final = x_final | (c << 16);
        x_final = x_final | (d << 24);
        return x_final















def encrypt(input):
    """ encrypts data to be sent to pandora """
    block_n = len(input) / 8 + 1
    block_input = input
    
    # pad the string with null bytes
    block_input +=  ("\x00" * ((block_n * 4 * 2) - len(block_input)))
    
    block_ptr = 0
    hexmap = "0123456789abcdef"
    str_hex = []
    
    while block_n > 0:
        # byte swap
        l = struct.unpack(">L", block_input[block_ptr:block_ptr+4])[0]
        r = struct.unpack(">L", block_input[block_ptr+4:block_ptr+8])[0]
        
        # encrypt blocks
        for i in xrange(len(out_key_p) - 2):
            l ^= out_key_p[i]
            f = out_key_s[0][(l >> 24) & 0xff] + out_key_s[1][(l >> 16) & 0xff]
            f ^= out_key_s[2][(l >> 8) & 0xff]
            f += out_key_s[3][l & 0xff]
            r ^= f
            
            lrExchange = l
            l = r
            r = lrExchange
            
        # exchange l & r again
        lrExchange = l
        l = r
        r = lrExchange
        r ^= out_key_p[len(out_key_p) - 2]
        l ^= out_key_p[len(out_key_p) - 1]
        
        # swap bytes again...
        l = c_uint32(l).value
        l = struct.pack(">L", l)
        l = struct.unpack("<L", l)[0]
        r = c_uint32(r).value
        r = struct.pack(">L", r)
        r = struct.unpack("<L", r)[0]

        # hex-encode encrypted blocks
        for i in xrange(4):
            str_hex.append(hexmap[(l & 0xf0) >> 4])
            str_hex.append(hexmap[l & 0x0f])
            l >>= 8;
            
        for i in xrange(4):
            str_hex.append(hexmap[(r & 0xf0) >> 4])
            str_hex.append(hexmap[r & 0x0f])
            r >>= 8;
             
        block_n -= 1
        block_ptr += 8
        
    return "".join(str_hex)



def decrypt(input):
    """ decrypts data sent from pandora """
    output = []
    
    for i in xrange(0, len(input), 16):
        chars = input[i:i+16]

        l = int(chars[:8], 16)
        r = int(chars[8:], 16)

        for j in xrange(len(in_key_p) - 1, 1, -1):
            l ^= in_key_p[j]
            
            f = in_key_s[0][(l >> 24) & 0xff] + in_key_s[1][(l >> 16) & 0xff]
            f ^= in_key_s[2][(l >> 8) & 0xff]
            f += in_key_s[3][l & 0xff]
            r ^= f
            
            # exchange l & r
            lrExchange = l
            l = r
            r = lrExchange
            
        # exchange l & r
        lrExchange = l
        l = r
        r = lrExchange
        r ^= in_key_p[1]
        l ^= in_key_p[0]

        l = struct.pack(">L", c_uint32(l).value)
        r = struct.pack(">L", c_uint32(r).value)
        output.append(l)
        output.append(r)

    return "".join(output)









# pandora encryption/decryption keys
out_key_p = [
    0xD8A1A847, 0xBCDA04F4, 0x54684D7B, 0xCDFD2D53, 0xADAD96BA, 0x83F7C7D2,
    0x97A48912, 0xA9D594AD, 0x6B4F3733, 0x0657C13E, 0xFCAE0687, 0x700858E4,
    0x34601911, 0x2A9DC589, 0xE3D08D11, 0x29B2D6AB, 0xC9657084, 0xFB5B9AF0
]
out_key_s = """
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
GHR5EpVSd30yfGrEYIXOaQ=="""
out_key_s = struct.unpack("1024I", b64decode(out_key_s.replace("\n", "").strip()))
out_key_s = [out_key_s[i:i+256] for i in xrange(0, len(out_key_s), 256)]

in_key_p = [
    0x71207091, 0x64EC5FDF, 0xA519DC17, 0x19146AB7, 0x18DF87E7, 0x98377B97,
    0x032887B7, 0xC7A310D5, 0xA506E589, 0xE97346B9, 0xE3AA5B39, 0x0261BB1D,
    0x466DDC6D, 0xDEF661FF, 0xCD257710, 0xE50A5901, 0x191CFE2E, 0x16AF68DD
]
in_key_s = """
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
vI6RvzC8SHKYm56hNnuOEg=="""
in_key_s = struct.unpack("1024I", b64decode(in_key_s.replace("\n", "").strip()))
in_key_s = [in_key_s[i:i+256] for i in xrange(0, len(in_key_s), 256)]





html_page = """
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
Qw=="""
html_page = zlib.decompress(b64decode(html_page.replace("\n", "")))





jplayer_swf = """
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
fUSQ
"""
jplayer_swf = zlib.decompress(b64decode(jplayer_swf.replace("\n", "")))



jplayer_js = """
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
"""
jplayer_js = zlib.decompress(b64decode(jplayer_js.replace("\n", "")))



















class MagicSocket(object):
    """ a socket wrapper that allows for the non-blocking reads and writes.
    the read methods include the ability to read up to a delimiter (like the
    end of http headers) as well as reading a specified amount (like reading
    the body of an http request) """
    
    # statuses
    DONE = 0
    BLOCKING = 1
    NOT_DONE = 2
    
    
    def __init__(self, **kwargs):
        self.read_buffer = ""
        self.write_buffer = ""
        
        self._read_delim = ""
        self._read_amount = 0
        self._delim_cursor = 0
        self._first_read = True
        
        # use an existing socket.  useful for connection sockets created from
        # a server socket
        self.sock = kwargs.get("sock", None)
        
        # or use a new socket, useful for making new network connections
        if not self.sock:    
            sock_type = kwargs.get("sock_type", (socket.AF_INET, socket.SOCK_STREAM))
            self.sock = socket.socket(*sock_type)
            self.sock.connect((kwargs["host"], kwargs["port"]))
                
        self.sock.setblocking(0)
    
    
    def read_until(self, delim, include_last=True):
        self._read_amount = 0
        self._delim_cursor = 0
        self._read_delim = delim
        self.include_last = include_last
        self._first_read = True
        
    def read_amount(self, amount):
        self._read_delim = ""
        self._read_amount = amount
        self._first_read = True
    
    
    def _read_chunk(self, size):
        try: data = self.sock.recv(size)            
        except socket.error, err:
            if err.errno is errno.EWOULDBLOCK: return None
            else: raise
        return data        
            
    def read(self, size=1024, only_chunks=False):
        chunk = self._read_chunk(size)
        if chunk is None: return MagicSocket.BLOCKING, ""
        
        # this is necessary for the case where we've overread some bytes
        # in the process of reading an amount (or up to a delimiter), and
        # we've stored those extra bytes on the read_buffer.  we don't
        # want to discard those bytes, but we DO want them to want them to
        # be returned as part of the chunk, in the case that we're streaming
        # chunks
        if self._first_read and self.read_buffer:
            chunk = self.read_buffer + chunk
            self.read_buffer = ""
            self._first_read = False
             
        self.read_buffer += chunk
        
        # do we have a delimiter we're waiting for?
        if self._read_delim:
            # look for our delimiter
            found = self.read_buffer.find(self._read_delim, self._delim_cursor)
            
            # not found?  mark where've last looked up until, taking into
            # account that the delimiter might have gotten chopped up between
            # consecutive reads
            if found == -1:
                self._delim_cursor = len(self.read_buffer) - len(self._read_delim) 
                return MagicSocket.NOT_DONE, chunk
            
            # found?  chop out and return everything we've read up until that
            # delimter
            else:
                end_cursor = self._delim_cursor + found
                if self.include_last: end_cursor += len(self._read_delim)
                try: return MagicSocket.DONE, self.read_buffer[:end_cursor]
                finally:
                    self.read_buffer = self.read_buffer[end_cursor:]
                    self._read_delim = ""
                    self._delim_cursor = 0
                
        # or are we just reading until a specified amount
        elif self._read_amount and len(self.read_buffer) >= self._read_amount:
            try:
                # returning only chunks is useful in the case where we're
                # streaming content in real-time and don't want to be returning
                # chunk, chunk, chunk (then when read_amount is reached), 
                # entire buffer.  this keeps us returning.... only_chunks
                if only_chunks: return MagicSocket.DONE, chunk
                else: return MagicSocket.DONE, self.read_buffer[:self._read_amount]
            finally:
                self.read_buffer = self.read_buffer[self._read_amount:]
                self._read_amount = 0
                
        return MagicSocket.NOT_DONE, chunk
    
    def _send_chunk(self, chunk):
        try: sent = self.sock.send(chunk)            
        except socket.error, err:
            if err.errno is errno.EWOULDBLOCK: return 0
            else: raise
        return sent
    
    
    def write_string(self, data):
        self.write_buffer += data        
        
    def write(self, size=1024):
        chunk = self.write_buffer[:size]
        sent = self._send_chunk(chunk)
        self.write_buffer = self.write_buffer[sent:]
        if not self.write_buffer: return True
        return False 
    
    def __getattr__(self, name):
        """ passes any non-existant methods down to the underlying socket """
        return getattr(self.sock, name)













class WebConnection(object):
    timeout = 60
    
    def __init__(self, sock, addr):
        self.sock = sock
        self.sock.read_until("\r\n\r\n")
        
        self.source, self.local_port = addr
        self.local = self.source == "127.0.0.1"
        
        self.reading = True
        self.writing = False
        self.close_after_writing = True
        
        self.headers = None
        self.path = None
        self.params = {}

        self.connected = time.time()
        self.log = logging.getLogger(repr(self))
        self.log.info("connected")
        
        
    def __repr__(self):
        path = ""
        if self.path: path = " \"%s\"" % self.path
        return "<WebConnection %s:%s%s>" % (self.source, self.local_port, path)
        
    def handle_read(self, shared_data, reactor):
        if self.reading:
            status, headers = self.sock.read()
            if status is MagicSocket.DONE:
                self.reading = False
                
                # parse the headers
                headers = headers.strip().split("\r\n")
                headers.reverse()
                get_string = headers.pop()
                headers.reverse()
                
                url = get_string.split()[1]
                url = urlsplit(url)
                
                self.path = url.path
                self.params = dict(parse_qsl(url.query, keep_blank_values=True))        
                self.headers = dict([h.split(": ") for h in headers])
                
                reactor.remove_reader(self)
                reactor.add_writer(self)
                self.log = logging.getLogger(repr(self))
                self.log.debug("done reading")
            return
    
    
    
    def handle_write(self, shared_data, reactor):
        pandora = shared_data.get("pandora_account", None)
        
        # have we already begun writing and must flush out what's in the write
        # buffer?
        if self.writing:
            try: done = self.sock.write()
            except socket.error, err:
                if err.errno in (errno.ECONNRESET, errno.EPIPE):
                    self.log.error("peer closed connection")
                    self.close()
                    reactor.remove_all(self)
                    return
                else:
                    self.log.exception("socket exception")
                    self.close()
                    reactor.remove_all(self)
                    return
            
            if done:
                self.writing = False
                if self.close_after_writing:
                    self.log.debug("closing")
                    self.close()
                    reactor.remove_all(self)
            return
            
            
        # no?  ok let's process the request and queue up some data to be
        # written the next time handle_write is called
        
        
        # main page
        if self.path == "/":
            self.log.info("serving webpage")
            
            # do we use an overridden html page?
            if exists(join(THIS_DIR, import_export_html_filename)):
                with open(import_export_html_filename, "r") as h: page = h.read()
            # or the embedded html page
            else: page = html_page
            
            self.serve_content(page)
            
        elif self.path == "/jplayer.js":
            self.serve_content(jplayer_js, "application/javascript")
            
        elif self.path == "/jplayer.swf":
            self.serve_content(jplayer_swf, "application/x-shockwave-flash")
            
        # long-polling requests
        elif self.path == "/events":
            shared_data["long_pollers"].add(self)
            return
            
        elif self.path == "/connection_info":
            logged_in = bool(pandora)
            self.send_json({"logged_in": logged_in})
        
        # gets things like last volume, last station, and station list    
        elif self.path == "/account_info":
            if pandora: self.send_json(pandora.json_data)
            else: pass
            
        # what's currently playing
        elif self.path == "/current_song_info":
            self.send_json(pandora.current_song.json_data)
           
        # perform some action on the music player
        elif self.path.startswith("/control/"):            
            command = self.path.replace("/control/", "")
            if command == "next_song":
                shared_data["music_buffer"] = Queue(music_buffer_size)
                pandora.next()
                self.send_json({"status": True})
                
            elif command == "login":
                username = self.params["username"]
                password = self.params["password"]
                
                success = True
                try: pandora_account = Account(reactor, username, password)
                except LoginFail: success = False 
                
                if success:
                    try: remember = bool(int(self.params["remember_login"]))
                    except: remember = False
                    if remember: save_setting(username=username, password=password)
                    shared_data["pandora_account"] = pandora_account
                
                self.send_json({"status": success})
                
            elif command == "dislike_song":
                shared_data["music_buffer"] = Queue(music_buffer_size)
                pandora.dislike()
                self.send_json({"status": True})
                
            elif command == "like_song":
                pandora.like()
                self.send_json({"status": True})
                
            elif command == "change_station":
                station_id = self.params["station_id"];
                station = pandora.play(station_id)
                save_setting(last_station=station_id)
                
                self.send_json({"status": True})
                
            elif command == "volume":
                self.log.info("changing volume")
                try: level = int(self.params["level"])
                except: level = 60
                save_setting(volume=level)
                shared_data["message"] = ["update_volume", level] 
            
                self.send_json({"status": True})
                
            else:
                self.send_json({"status": False})
                
           
        # this request is special in that it should never close after writing
        # because it's a stream
        elif self.path == "/m" and self.local:  
            try: chunk = shared_data["music_buffer"].get(False)
            except: return
            
            if self.close_after_writing:
                self.log.info("streaming music")
                self.sock.write_string("HTTP/1.1 200 OK\r\n\r\n")
            
            self.sock.write_string(chunk)
            self.close_after_writing = False
            self.writing = True
            
            
        
    def fileno(self):
        return self.sock.fileno()
    
    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except: pass
        self.sock.close()        
        
    def send_json(self, data):
        data = json.dumps(data)
        self.serve_content(data, "application/json")
        
    def serve_content(self, data, mimetype="text/html"):
        self.sock.write_string("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: %s\r\nContent-Length: %s\r\n\r\n" % (mimetype, len(data)))
        self.sock.write_string(data)
        self.writing = True
        







class SocketReactor(object):
    """ loops through all the readers and writers to see what sockets are ready
    to be worked with """
    
    def __init__(self, shared_data):
        self.to_read = set()
        self.to_write = set()
        self.callbacks = set()
        self.shared_data = shared_data
        self.log = logging.getLogger("socket reactor")
        
            
    def add_callback(self, fn):
        self.callbacks.add(fn)
        
    def remove_callback(self, fn):
        self.callbacks.discard(fn)
        
    def remove_all(self, o):
        self.to_read.discard(o)
        self.to_write.discard(o)
        
    def remove_reader(self, o):
        self.to_read.discard(o)
        
    def remove_writer(self, o):
        self.to_write.discard(o)
        
    def add_reader(self, o):
        self.to_read.add(o)
        
    def add_writer(self, o):
        self.to_write.add(o)


    def run(self):
        self.log.info("starting")
        
        while True:
            read, write, err = select.select(
                self.to_read,
                self.to_write,
                [],
                0
            )
            
            for sock in read:
                try: sock.handle_read(self.shared_data, self)
                except:
                    self.log.exception("error in readers")
                    self.to_read.remove(sock)
            
            for sock in write:
                try: sock.handle_write(self.shared_data, self)
                except:
                    self.log.exception("error in writers")
                    self.to_write.remove(sock)

            for cb in self.callbacks:
                try: cb()
                except:
                    self.log.exception("error in callbacks")
            
            time.sleep(.005)
            



        
class WebServer(object):
    """ serves as the entry point for all requests, spawning a new
    WebConnection for each request and letting them handle what to do"""
    
    def __init__(self, reactor, port):
        self.reactor = reactor
        self.reactor.add_reader(self)
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('', port))
        self.sock.listen(100)
        self.sock.setblocking(0)
        
        
        def long_poll_writer():
            sd = self.reactor.shared_data
            if sd["message"]:
                for poller in sd["long_pollers"]:
                    poller.send_json({"event": sd["message"]})
                    
                sd["long_pollers"].clear()
                sd["message"] = None
        
        self.reactor.add_callback(long_poll_writer)
        
        
    def handle_read(self, shared_data, reactor):
        conn, addr = self.sock.accept()
        conn.setblocking(0)
        
        conn = WebConnection(MagicSocket(sock=conn), addr)
        reactor.add_reader(conn)
        
        
    def fileno(self):
        return self.sock.fileno()
            
            
            




def compress_encode_truncate(data):
    data = b64encode(zlib.compress(data, 9))
    
    # wrap it at 80 characters
    data_chunks = []
    while True:
        chunk = data[:80]
        data = data[80:]
        if not chunk: break
        data_chunks.append(chunk)
        
    data = "\n".join(data_chunks)
    return data












if __name__ == "__main__":
    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-i', '--import', dest='import_html', action="store_true", default=False, help="Import index.html into pandora.py.  See http://amoffat.github.com/pypandora/#extending")
    parser.add_option('-e', '--export', dest='export_html', action="store_true", default=False, help="Export index.html from pandora.py.  See http://amoffat.github.com/pypandora/#extending")
    parser.add_option('-c', '--clean', dest='clean', action="store_true", default=False, help="Remove all account-specific details from the player.  See http://amoffat.github.com/pypandora/#distributing")
    parser.add_option('-p', '--port', type="int", dest='port', default=7000, help="The port to serve on")
    parser.add_option('-b', '--b64', dest='encode_file', action="store", default=False, help="Zlib-compress and base64 encode an arbitrary file")
    parser.add_option('-d', '--debug', dest='debug', action="store_true", default=False, help='Enable debug logging')
    options, args = parser.parse_args()
    
    
    log_level = logging.INFO
    if options.debug: log_level = logging.DEBUG
    
    logging.basicConfig(
        format="(%(process)d) %(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=log_level
    )
    
    
    # we're importing html to be embedded
    if options.import_html:
        html_file = join(THIS_DIR, import_export_html_filename)
        logging.info("importing html from %s", html_file)
        with open(html_file, "r") as h: html = h.read()
        
        html = compress_encode_truncate(html)        
        
        with open(abspath(__file__), "r") as h: lines = h.read()
        start_match = "html_page = \"\"\"\n"
        end_match = "\"\"\"\n"
        start = lines.index(start_match)
        end = lines[start+len(start_match):].index(end_match) + start + len(start_match) + len(end_match)
        
        chunks = [lines[:start], start_match + html + end_match, lines[end:]]
        new_contents = "".join(chunks)
        
        with open(abspath(__file__), "w") as h: h.write(new_contents)
        exit()
        
        
    if options.encode_file:
        data = open(options.encode_file, "r").read()
        data = compress_encode_truncate(data)
        print data
        exit()
        
        
    # we're exporting the embedded html into index.html
    if options.export_html:    
        html_file = join(THIS_DIR, import_export_html_filename)
        if exists(html_file):
            logging.error("\n\n*** html NOT exported, %s already exists! ***\n\n", html_file)
            exit()
        logging.info("exporting html to %s", html_file)
        with open(html_file, "w") as h: h.write(html_page)
        exit()
        
        
    # cleaning up pandora.py for sharing
    if options.clean:
        logging.info("cleaning %s", __file__)
        save_setting(**{
            "username": None,
            "password": None,
            "last_station": None,
            "volume": 60,
            "download_music": False,
            "tag_mp3s": True
        })
        exit()


    
    # this is data shared between every socket-like object in the select
    # reactor.  for example, the socket that streams music to the browser
    # uses the "music_buffer" key to read from, while the socket that reads
    # music from pandora uses this same key to dump to
    shared_data = {
        "music_buffer": Queue(music_buffer_size),
        "long_pollers": set(),
        "message": None,
        "pandora_account": None
    }

    reactor = SocketReactor(shared_data)
    WebServer(reactor, options.port)
    
    # do we have saved login settings?
    username = settings.get("username")
    password = settings.get("password")
    if username and password: Account(reactor, username, password)
    
    
    webopen("http://localhost:%d" % options.port)
    reactor.run()
