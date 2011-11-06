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
    'tag_mp3s': False,
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
        if self.current_song:
            self.current_song.like()
            return True
        return False   
        
    def dislike(self):
        if self.current_song:
            self.current_song.dislike()
            self.next()
            return True
        return False   
        
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
            "rating": self.rating,
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
        self._add_feedback(like=True)

    def dislike(self, **kwargs):
        self.log.info("disliking")
        self._add_feedback(like=False)
        return self.station.next(**kwargs)

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
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"""
html_page = zlib.decompress(b64decode(html_page.replace("\n", "")))












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
            self.serve_webpage()
            
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
        self.sock.write_string("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n" % len(data))
        self.sock.write_string(data)
        self.writing = True

    def serve_webpage(self):
        # do we use an overridden html page?
        if exists(join(THIS_DIR, import_export_html_filename)):
            with open(import_export_html_filename, "r") as h: page = h.read()
        # or the embedded html page
        else: page = html_page
        
        self.sock.write_string("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: %s\r\n\r\n" % len(page))
        self.sock.write_string(page)
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
            
            
            














if __name__ == "__main__":
    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-i', '--import', dest='import_html', action="store_true", default=False, help="Import index.html into pandora.py.  See http://amoffat.github.com/pypandora/#extending")
    parser.add_option('-e', '--export', dest='export_html', action="store_true", default=False, help="Export index.html from pandora.py.  See http://amoffat.github.com/pypandora/#extending")
    parser.add_option('-c', '--clean', dest='clean', action="store_true", default=False, help="Remove all account-specific details from the player.  See http://amoffat.github.com/pypandora/#distributing")
    parser.add_option('-p', '--port', type="int", dest='port', default=7000, help="The port to serve on")
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
        html = b64encode(zlib.compress(html, 9))
        
        # wrap it at 80 characters
        html_chunks = []
        while True:
            chunk = html[:80]
            html = html[80:]
            if not chunk: break
            html_chunks.append(chunk)
        html = "\n".join(html_chunks)
        
        
        with open(abspath(__file__), "r") as h: lines = h.read()
        start_match = "html_page = \"\"\"\n"
        end_match = "\"\"\"\n"
        start = lines.index(start_match)
        end = lines[start+len(start_match):].index(end_match) + start + len(start_match) + len(end_match)
        
        chunks = [lines[:start], start_match + html + end_match, lines[end:]]
        new_contents = "".join(chunks)
        
        with open(abspath(__file__), "w") as h: h.write(new_contents)
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
            "download_music": False
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
