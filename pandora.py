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
TEMPLATE_DIR = join(THIS_DIR, "templates")

music_buffer_size = 20




# settings
settings = {
    'volume': '21',
    'download_music': False,
    'download_directory': '/tmp',
    'last_station': '289904271165618004',
}




def save_setting(key, value):
    """ saves a value persisitently *in the file itself* so that it can be
    used next time pypandora is fired up.  of course there are better ways
    of storing values persistently, but i want to stick with the '1 file'
    idea """
    global settings
    
    logging.info("saving value %r to %r", value, key)
    with open(abspath(__file__), "r") as h: lines = h.read()
    
    
    start = lines.index("settings = {\n")
    end = lines[start:].index("}\n") + start + 2
    
    chunks = [lines[:start], "", lines[end:]]
    
    settings[key] = value
    new_settings = "settings = {\n"
    for k,v in settings.iteritems(): new_settings += "    %r: %r,\n" % (k, v)
    new_settings += "}\n"
    
    chunks[1] = new_settings
    new_contents = "".join(chunks)
    
    with open(abspath(__file__), "w") as h: h.write(new_contents)





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

    def __init__(self, debug=False):
        self.debug = debug
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
        if self.debug:
            debug_logger = logging.getLogger("debug_logger")
            debug_logger.debug("sending data %s" % self.dump_xml(body))

        body = encrypt(body)
        conn.request("POST", url, body, headers)
        resp = conn.getresponse()

        if resp.status != 200: raise Exception(resp.reason)

        ret_data = resp.read()

        # debug logging?
        if self.debug:
            debug_logger = logging.getLogger("debug_logger")
            debug_logger.debug("returned data %s" % self.dump_xml(ret_data))

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
    def __init__(self, email, password, debug=False):
        self.log = logging.getLogger("account %s" % email)
        self.connection = Connection(debug)        
        self.email = email
        self.password = password
        self._stations = {}
        self.recently_played = []

        self.current_station = None
        
        # this is used just for its fileno() in the case that we have no current
        # song.  this way, the account object can still work in select.select
        # (which needs fileno())
        self._dummy_socket = socket.socket()
        self.login()
        
    def handle_read(self, to_read, to_write, to_err, shared_data):
        if shared_data["music_buffer"].full(): return
        chunk = self.current_song.read()
        
        if chunk: shared_data["music_buffer"].put(chunk)
        # song is done
        elif chunk is False and self.current_song.done_playing:
            shared_data["music_buffer"] = Queue(music_buffer_size)
            self.current_station.next()
        
    def next(self):
        if self.current_station: self.current_station.next()
        
    @property
    def current_song(self):
        return self.current_station.current_song

    def fileno(self):
        if self.current_song: return self.current_song.fileno()
        else: self._dummy_socket.fileno()
            
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
        if not logged_in: raise Exception, "can't log in.  wrong username or password?"
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
        
        self.log = logging.getLogger(repr(self))

    def like(self):
        # normally we might do some logging here, but we let the song object
        # handle it
        self.current_song.like()

    def dislike(self):
        self.current_song.dislike()
        self.next()
    
    def play(self):
        if self.account.current_station and self.account.current_station is not self:
            self.log.info("changing station to %r", self)
            
        self.account.current_station = self
        
        self.playlist.reverse()
        if self.current_song: self.account.recently_played.append(self.current_song)
        self.current_song = self.playlist.pop()
        self.log.info("playing %r", self.current_song)
        self.playlist.reverse()
        self.current_song.play()
            
    def next(self):
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
                    c = member.getchildren()
                    song_params[c[0].text] = c[1].text
                song = Song(self, **song_params)
                self._playlist.append(song)

            if self._playlist:
                got_playlist = True
                break
            else:
                self.log.error("failed to get playlist, trying %d more times" % get_playlist_tries)
                self.account.login()

        if not got_playlist: raise Exception, "can't get playlist!"
        return self._playlist

    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)

    def __str__(self):
        return "%s" % self.name




class Song(object):
    bitrate = 128
    read_chunk_size = 1024
    

    def __init__(self, station, **kwargs):
        self.station = station

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
        self.song_size = None
        self.download_progress = 0

        def format_title(part):
            part = part.lower()
            part = part.replace(" ", "_")
            part = re.sub("\W", "", part)
            part = re.sub("_+", "_", part)
            return part

        self.filename = join(settings["download_directory"], "%s-%s.mp3" % (format_title(self.artist), format_title(self.title)))

        self._stream_gen = None
        self.sock = None
        self.read()        
        self.log = logging.getLogger(repr(self))
        
        
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
    def play_progress(self):
        now = time.time()
        return 100 * self.position / self.duration
    
    @property
    def done_playing(self):
        return time.time() - self._started_streaming >= self.duration
    
    @property
    def done_downloading(self):
        return self.download_progress == self.song_size
    
    def read(self):
        if not self._stream_gen: self._stream_gen = self._stream()
        try: data = self._stream_gen.next()
        except StopIteration: return False
        return data
        
    def fileno(self):
        return self.sock.fileno()
    
    
    def stop(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()
        
    
    def play(self):
        self.station.current_song = self
        

    def _stream(self):
        """ a generator which streams some music """  

        # figure out how fast we should download and how long we need to sleep
        # in between reads.  we have to do this so as to not stream to quickly
        # from pandora's servers        
        bytes_per_second = self.bitrate * 125.0
        sleep_amt = Song.read_chunk_size / bytes_per_second
        
        # so we know how short of time we have to sleep to stream perfectly,
        # but we're going to lower it, so we never suffer from
        # a buffer underrun
        sleep_amt *= .8


        split = urlsplit(self.url)
        host = split.netloc
        path = split.path + "?" + split.query



        # this is a little helper function because we might need to reconnect
        # a few times if a read fails.  we'll just pass in the byte_counter
        # to pick back up where we left off
        def reconnect():
            req = """GET %s HTTP/1.0\r\nHost: %s\r\nRange: bytes=%d-\r\nUser-Agent: pypandora\r\nAccept: */*\r\n\r\n"""
            sock = MagicSocket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, 80))
            sock.send(req % (path, host, self.download_progress))
            
            # we wait until after we have the headers to switch to non-blocking
            # just because it's easier that way.  in the worst case scenario,
            # pandora's servers hang serving up the headers, causing our app to hang
            headers = sock.read_until("\r\n\r\n", include_last=True)
            headers = headers.strip().split("\r\n")
            headers = dict([h.split(": ") for h in headers[1:]])
            sock.setblocking(0)
            return sock, headers
        
        self.sock, headers = reconnect()
        yield None
        self.log.info("downloading")
        
        # determine the size of the song, and from that, how long the song is
        # in seconds
        self.song_size = int(headers["Content-Length"])
        self.duration = self.song_size / bytes_per_second
        read_amt = self.song_size


        mp3_data = []
        self.download_progress = 0
        self._started_streaming = time.time()
        last_read = 0
        
        # do the actual reading of the data and yielding it.  if we're
        # successful, we yield some bytes, if we would block, yield None,
        while not self.done_downloading:
            
            # check if it's time to read more music yet.  preload the
            # first 128k quickly so songs play immediately
            now = time.time()
            if now - last_read < sleep_amt and self.download_progress > 131072:
                yield None
                continue
            
            # read until the end of the song, but take a break after each read
            # so we can do other stuff
            last_read = now
            chunk = self.sock.read_until(read_amt, break_after_read=True, buf=Song.read_chunk_size)
            
            # got data?  aggregate it and return it
            if chunk:
                self.download_progress += len(chunk)
                mp3_data.append(chunk)
                yield chunk
                
            # disconnected?  do we need to reconnect, or have we read everything
            # and the song is done?
            elif chunk is False:
                if not self.done_downloading:
                    self.log.error("disconnected, reconnecting at byte %d of %d", self.download_progress, self.song_size)
                    self.sock, headers = reconnect()
                    read_amt = int(headers["Content-Length"])
                    continue
                # done!
                else: break
                
            # are we blocking?  this is normal, keep going
            elif chunk is None:
                continue
            
            
            
        if settings["download_music"]:
            self.log.info("saving file to %s", self.filename)
            mp3_data = "".join(mp3_data)
            
            # tag the mp3
            tag = ID3Tag()
            tag.add_id(self.id)
            tag.add_title(self.title)
            tag.add_album(self.album)
            tag.add_artist(self.artist)
            # can't get this working...
            #tag.add_image(self.album_art)
    
            # and write it to the file
            h = open(self.filename, "w")
            h.write(tag.binary() + mp3_data)
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
    """ encrypts data to be sent to pandora. """
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
    """ decrypts data sent from pandora. """
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
lUGwU09m2DT5pk9WYUI6lBIx7kNhm7wvLyvJMYXkI1+u9VEdU5hYRfW+eewEaeVkuE+50ob37BJcsfs5
3yLIrdC4PvYDbn5wO5buyTtTTK9dKcq29sieZtooIVtCcMzNaOro7CdCVrbpHS0EOE27p13yKnCVgSEE
YLtd2ohhdwXUVPvm83PS0Fw5mPRjqv/SAF/MLqtaeJsO8Y3op8WlRva6BctmdNCTL+uC/VxSxykWEhWI
A2jqfwcVr3mZ+e6rkY0zLC0R3IvxnWOuHXeVM3hZ0OXO+12YLHEzegAI7nenNTJqihclrZy56l0eNRil
nMKR4bX3WI8aMFmPF3cNIykJSDalnzjnCRIQderWgMwBcqcgf8w03+wVDd3Xm9OqyMFI4R49BWCqu2Wn
foBaBZH1PiQYo8Y7qOK0hgaNBjbt5ziOQxzv9hwtwUve1FzuH15jpT8Qp07VwnzjUtEkqqElDcHdsaS1
r+igOJJt6cKM2zfFOf0A+x/j05bV0YcVYmE8PSGabhFaoXNe9gcS++aMXCD0uC7NU44tv7aZB8B6ZasF
YHYzWlNn9hP/aZl2kpguEY+WAOliVJ7AzR09+O4wh8439am4+QdKfSq7hegyKa5sC/Kflaf1byZ1XUbS
zVC6IEq0rT3uOS3nnnU9G1hyU09QOUAPLEv25yTVM+AJYP8HsnCCLIUwpGrlnWVWheqCIKt/ND31PZAs
OUu15/O216rf9b0Q+AWEnwFXY3SjRcm7wmcP71Pjz47UR3nEMoljy33S3+Dzzw45/0GZMuGyuLdDmBKW
AHxIbQMYo/eN9NXv1IFIFJefyYI8I6Y8gNiBXW7IUlRLQveSMIK/Gk6EnSuCEBVTIDfb/87YmFNm27HS
3+5/Y3MYKAwPCFsNGUjIHG5Bxqmib70MZR8xXarkEBvn/C6GoY4ruE3LbyxydhlIofXpTYcVmhh4J7gR
oiAG30e1no8IvEIMm2s475G6gigkaLFcKEKwlUTHhArxEk9KHRIoM1gMnQkwQ/6feGhnU23+Sw96dfb3
H2qehK8F8+cKz+WrHz3H1IqiG+xgHEjf6WkBOgZfT2SZOKB0KsQcLnoeGL/fMdC/rb/5qLz5j7CmQPAH
DSSomqYwZ5OunGVL/y15cJONc1Df+QIugar2AeZXVaqOWN/1eyHTcCzP0r+8qJNhrQ0dTAFvYo5wj4uH
7F8rQmjTpXeESlqcAwsmMTgnCqAcUxr+E4AmjYeQbZJyxMxmXbzmoGyAtHJuyF7XbJ2q4pTTjV+BKclw
dYxXMk8OES4/iPAg9UBXUPd2K9VPfghM7lVkbV+Jni7DqCbY5lIhA53XvOOqlme6jczy4TUHp2GFihpY
f5NK/7ZQ08tXdSEE3r5ICwZ42If2goLnYecYFXUtJNBWpuj+nBHv4V8KXUaYpyeGWZRYstL0i2oF5vKu
YkQ1IgDetaO+hgDE+qRPq6SCx9f3A1AJkQpVdqZC11GMhrHmG4kuejJMwjJOtR63MPLxWHtCcyyxLa9s
i4RlbhjMLyB6XC54O6A3zE392eHo64y6En5duvNgfuOuQcqZGhQPt+blmTMWhBZv/c382fCeFOCORTJm
wnIsGdSoN7v6bOtNvullHZ00R5/MV0i0QbtO7gr4cVUaEmBw5apjGKBN5ImbcKuontSwyK1NCnq9Tr9T
PEws9ZcB4BPqSMf1ej6ZAU/z7cu8t6bF1K/3zlhEVf9f+4GzLLO0E4gqfk5PQxVQcl57l9U3UFnuFJmq
Ss2O5Cgxk7WXx5uBURTaH9BvJH6CP65w5L++Okqpm+h/pYsP0d8urdFIwnGLWDHeuKxYkGEbgR1Gl0Vq
d4tpdRopxQYHx/3EovcSNLcsHaHZNRx3uVJS+7vG3Iofsf2sLhA9pXpr3Tu830JyNq8OYNebOVUC2VJc
SKXV5liaWeOwgoHpGAyMdAXuK0vYHVPMjB8jo5CT0o/7Q4z9SRAifu+dSM2RyRIjCDJjVT8WFDVZ7jur
e6r/d0xakZBK+T8hBj/NPOn0TVdz4m4lkeb+uYAPmpdth6T7uEg0yo54BZqFeYEBhVLJ/d2r/WQu/45+
sN/4yQ8phTXN6Vmjzho255feBQ+flEqo0XlvgXk/BYAxFe3zXmd3ABx9QKpO7AWpSRoDtQgDhPeMBkkX
tJS550b3Y3kug9b4czvE8lmIhQzfn8qKgLCAqag7qC3+5qmIyl3WoXhpRrfVeXUc5ynoo2KnOKAi/Gwu
3cKjgKIFtW/JCahAWjShRQTtH8r5RD9ijP3svNijHrxOnPo8cUv4w8OtXiRNkzUG8l4R/Z2eouYyWhM7
h2IxVN2yJ2SKrViahfbEGFlVzwscfJMf4q3r9/webbtuB8NeKMIwsycLY/stLVi60KgQaHRoQpNN6/Qs
3QxlvJOk4m37sOZ/2+VR8r7WEm+FdLrGRZxBX3zwsCI82+2SV9Jp8eI6ZjKiTrcEeHOjyHK8UgbH/QK0
aIJ/3wMPA0SS0z+vlMGRVM65XbiPylHWDAVVEspGmMIynYNMFH5bP4Z1hyoeJIr5QDIpnq/q/aGMCqMg
U4C9HIEb9TSQzYUjIDmqljC+4vUlZjNJrHyMjWbiGOJL+woUzm1x41dDjcc4haDHzIItAWefB65Nf8kf
9iy4E/moUKSRuPI+cGgBN0d9g4W5VAWeJQQlXvU8TZIATKgajC+0JxA2Akl7O7en/bzIYi1HMTbW0jMO
CksLCsNWJWq/IzfRp8UUlIr5/2coUZQBjJI30APDx96sNr1ey1uQXCZkCwJNxcm3JvkThhiR7B9hqqFR
GBC6FsKPM2jtoUJVQuljgKJArotUaV0cXaxjpNgeMDwKhktPfF7kbmjuYtQOL6jfz7c+drxfM3hk0O6V
1nkv+2ojquyunkVZaiM9IwqQsk20Xp2LtqshD7HyJ/pR+qakIf1TRibF6ZMbNMvMGnFmH75U4GhD/QRy
wIqeaBstMBI5EtGWOoPas07RFcqLedSATVxGtqFQrdpeudafy/5OGxyqYer6d60YGGFzWoUzGlGFKtnV
PgRXuaZUNarwHVf7/oZaMHJFDiSW/t6AzPWIKOXaclJJNygaFOD1DX0vbx/HHSwpFNUux+Wa6Vln3k8M
QyYL42ovwST8C5tMAxqPp9/BNgAJczuRiVrefwTVyf/bQnyfKkvoi/q8i1jnJiyHQY+8o7RgEVvkMJsj
lFTdsg9TN+MzzRPx343c6p+dM/99WvFxa+FzWQo/TVo4YmUo6Xa2DqKpM1U19eoHkRonXM81+to0TgyM
IFDRspOvzqm0VxEnY4m4QSQG6oh8DECUcM0NZd/E+LqJF9wTK1JAAMHQ4RNcxcKAK7kiTCz9vcknq3Sr
2GqvrpPopaINAKkQlEfk36CfT/mlFnP8dXOWew1wFAFjc/yrFYnLCTqbbLm9QlHpISpjG4hipletKrKA
zNt2wjXQOIzOyK61MutQQRIV/ugD5U8YqVdqxvQrmSUAgW90kQ55xyeCmOgSGGxifANgqup7mUN3qAiV
Rg7oWv9YJ97unuj31ofjDnL4Y9c48teX1YIIkUuZVGczdGxDWgshk/frM8wwuTDFcreoXSPsXTJ7zJlF
zJGFRQoTSVrcgH+4zgiCcD8DtuRmE+mv8j6ry7iEi3FenIVg7BpwUEjK4gW4zb+ygchH/QLMjrFyvKr4
S2W9CsACZhrXzvv/SHRi2gpZ4GfZUhk7+zIKTHxQ8OnWmQaDKR5Izwdo7D9xTssYOVisbNVfLlmiHSsa
D4QEoVKjtOLyLSCqN9jjwTovoE3iPq64j63yupykoGCii6AD/BN2PKdaK8QEmnn7Ty7BPxqI+OKTb0uF
YrYucw2xTPBA/b07vQgfm0wFnme5gVxaWvTuQoLCyuFRcwWOQDGPYb63TisWRAt8IfV81mgpsWEyR0US
tRfhCeIFf0KgVhIVhxDe1FUuD2Hm7QMHKNOE2VSXND8F++d2kpIzjAAsexw5Q9PqOyvW7t4w1Irue5dW
a7k9xyb07HQK8ifss7FQ0jZ029H7mN+6t/TbtcKJdchC2TTW9P6itTvx1n9No524/ErBn9RjNXNq7uDO
vsyZVpw0hNbszCz95YnpnFbLoCcoCeACaGu6q8/8Ide3p5amYx3ONteHQpvRQwc5Nrv2acUh1ZOtYtFE
qQoH4MadtV85LlQZvoj3JtaJ/nXLz4AMjNxABVX3VPqC/HP3ClddoyIzcsXee+8lCuj4h9dgaZRr19sa
+RoMdWvEYAOp0kaEBisgyTqEjCfPfKBdqtRFIiL135PvqioZ2V3enMKUlz2Qb+HzU8bMNsBJafGITlXY
juwkWOWqEeMNerz9N630EJT0aPSdgU9O6leeP63EPuR2IB+H8bTdSrOZ4ug2GAp8oTYflA97RTXsCkc5
S1Bbmvj2skuLWd72f8D+jL/USEUpAh7U9xIqP9Wb8Rq1dUFk/IXKpNZvJ0lSTSr2RmgKIcPMVruyWBED
NSPGTQ6h/xDvvFXwKHmqxTz3NBSo7EMqrDIhhCGtYtV8xM7VsBppGzVwdkLJWboTdT2vKVAonrOlktQx
UiOefeEj+WoZ1OE7fooVFnZz70TBxuMuFiY1jboptpytuQhiaR9jCzIfV48Mi9awoNP6zf51CMgvYOlZ
mrb2UVWUQB1ctf1h4yA3zpDnfRMb/ASLiuOcwfmQnbIkNVnTHJUnFt6lEVybQJSvcbYyyOMqtzKAtmu6
is8qoT/UqeS5gXsQ1bmxU7kLoI0hiRQspsEeFc3saNe3VWhDNKMzjqECtetX0cwu9lj5OVteMpr+U+7a
Fux4Avtb3q1diEiS4RgyQX7zYxx8dAxN9Lo1ke3ubtjFMdRzFowyKCkiz2sN29xG3FAa36zDYJh91oxw
AySH9phuLVIPpc7mB9zJqo1eYNWnLG7lfSL9R90NIfhMlz8PEW4uBkNP/07jRRmmUP9Z7e3blGDgOjhw
R1+14nArIYE9TXvUkv17cuwHemB7qUruJsyf/qTuL7vM5MQxGc9qoIOpqNfqOKDHPMuJvPY7xlgNfgrG
36nCHXiCfF/6Mmth4qV1NEyPimDXncF+FsfSDI6CRq76FzvlTa2k9a6QEgvTdG5cFnNthvFNBTm7GKii
OsPWQvLA/rZytdOhPq1Ib0qmRGFza/h7xEtEDkod0JrrxMNDzY9RnSC34Rxwn1etT8vussevApWgR8br
XBv6t25PzDpIcX4EyXpuSWRkE392bsNBXss4zESUT7Kx45UuMOV93sN0ys0EpUoEHzVL+qI9w/usXfsU
yDmbF+GFSr0Rrfo+cW8sLMQyKulQwbZ2LCH+4c/Uj/PZNYwmCDOw7bUsjns5aHCzIOREgSfPzvZdIuUN
VM1AXVyWQot26ZVyJ2hNhN8jHojxRUqW6ISOUp852ld6PJDX4f6weNel07jhnZos/bFzTYGDVePhNASL
vI6RvzC8SHKYm56hNnuOEg=="""
in_key_s = struct.unpack("1024I", b64decode(in_key_s.replace("\n", "").strip()))
in_key_s = [in_key_s[i:i+256] for i in xrange(0, len(in_key_s), 256)]






html_page = """
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
"""
html_page = zlib.decompress(b64decode(html_page.replace("\n", "")))










class MagicSocket(socket.socket):
    """ this is a socket subclass that simplifies reading until a specific
    delimeter (for example, end of http headers) and reading until a specific
    amount has been read (for example, reading the body of an http response
    based on the content-length in the http headers).  it gets kind of
    complicated when you add in non-blocking sockets..."""
    
    def __init__(self, *args, **kwargs):
        self.tmp_buffer = ""
        self._read_gen = None
        
        sock = kwargs.get("sock")
        if sock: self._sock = sock
        else:
            self._sock = self
            super(MagicSocket, self).__init__(*args, **kwargs)
            
    def __getattr__(self, name):
        return getattr(self._sock, name)
        
    def read_until(self, *delims, **kwargs):
        if not self._read_gen: self._read_gen = self._read_until(*delims, **kwargs)
        ret =  self._read_gen.next()
        if ret: self._read_gen = None
        return ret
        
    def _read_until(self, *delims, **kwargs):
        buf = kwargs.get("buf", 1024)
        break_after_read = kwargs.get("break_after_read", False)
        include_last = kwargs.get("include_last", False)

        num_bytes = 0
        if len(delims) == 1 and isinstance(delims[0], int):
            num_bytes = delims[0]
            
        read = ""
        cursor = 0
        last_cursor = None
        first_find = None
        
        delims = list(delims)
        delims.reverse()
        current_delim = delims.pop()
        
        def recv(buf):
            read = ""
            if self.tmp_buffer:
                read += self.tmp_buffer[:buf]
                self.tmp_buffer = self.tmp_buffer[buf:]
                buf -= len(read)
                if not buf: return read     
            return read + self._sock.recv(buf)
        
        while True:
            if not num_bytes:
                # search through the data we have for the delimiters
                lread = len(read)
                while cursor < lread and cursor != last_cursor:
                    found = read.find(current_delim, cursor)
                    last_cursor = cursor
                    if found == -1:
                        cursor = lread - len(current_delim)
                    else:
                        if first_find is None: first_find = found
                        cursor = found + len(current_delim)
                        try: current_delim = delims.pop()
                        except IndexError:
                            if first_find == found: first_find = 0
                            
                            if include_last:
                                self.tmp_buffer = read[found+len(current_delim):]
                                yield read[first_find:found+len(current_delim)]
                            else:
                                self.tmp_buffer = read[found:]
                                yield read[first_find:found]
             
                last_cursor = None
            
            
            try: data = recv(buf)
            except socket.error, err:
                if err.errno is errno.EWOULDBLOCK: yield None
                else:
                    yield False
            else:
                if not data: yield False
                if break_after_read: yield data
                
                read += data
                if num_bytes and len(read) >= num_bytes and not break_after_read:
                    self.tmp_buffer = read[num_bytes:]
                    yield read[:num_bytes]














class WebConnection(object):
    timeout = 60
    
    def __init__(self, sock, source, pandora_account):
        self.pandora_account = pandora_account
        self.sock = sock
        self.source = source
        self.local = self.source == "127.0.0.1"
        
        self.headers = None
        self.path = None
        self.params = {}
        self._request_gen = None
        
        self._stream_gen = None
        self.connected = time.time()
        
        
    def handle_read(self, to_read, to_write, to_err, shared_data):
        ret = self.request_read
        if ret:
            to_read.remove(self)
            to_write.add(self)
        elif ret is False:
            to_read.remove(self)
    
    def handle_write(self, to_read, to_write, to_err, shared_data):
        if self.path == "/":
            self.serve_webpage()
            self.close()
            to_write.remove(self)
            to_err.remove(self)
            
        # long-polling requests
        elif self.path == "/events":
            shared_data["long_pollers"].append(self)
            #self.close()
            #to_write.remove(self)
            #to_err.remove(self)
            
        elif self.path == "/account_info":
            self.send_json(self.pandora_account.json_data)
            self.close()
            to_write.remove(self)
            to_err.remove(self)
            
        elif self.path == "/current_song_info":
            self.send_json(self.pandora_account.current_song.json_data)
            self.close()
            to_write.remove(self)
            to_err.remove(self)
           
        elif self.path.startswith("/control/"):            
            command = self.path.replace("/control/", "")
            if command == "next_song":
                shared_data["music_buffer"] = Queue(music_buffer_size)
                self.pandora_account.next()
                
            elif command == "change_station":
                station_id = self.params["station_id"];
                station = self.pandora_account.stations[station_id]
                save_setting("last_station", station.id)
                station.play()
                
            elif command == "volume":
                level = self.params["level"]
                save_setting("volume", level)
            
            self.send_json({"status": True})
            self.close()
            to_write.remove(self)
            to_err.remove(self)
           
        elif self.path == "/m" and self.local:            
            try: chunk = shared_data["music_buffer"].get(False)
            except: return
            
            done = self.stream_music(chunk)
            if done:
                self.close()
                to_write.remove(self)
                to_err.remove(self)
           
        else:
            self.close()
            to_write.remove(self)
            to_err.remove(self)
                   
        
    def fileno(self):
        return self.sock.fileno()
    
    @property
    def request_read(self):
        if not self._request_gen: self._request_gen = self.read_request()
        return self._request_gen.next()
    
    def close(self):
        try: self.sock.shutdown(socket.SHUT_RDWR)
        except: pass
        self.sock.close()
    
    def read_request(self):
        headers = None
        
        while not headers:
            headers = self.sock.read_until("\r\n\r\n", include_last=True)
            if headers is None: yield None
            elif headers is False:
                yield False
                raise StopIteration
        
        headers = headers.strip().split("\r\n")
        headers.reverse()
        get_string = headers.pop()
        headers.reverse()
        
        url = get_string.split()[1]
        url = urlsplit(url)
        
        self.path = url.path
        self.params = dict(parse_qsl(url.query))        
        self.headers = dict([h.split(": ") for h in headers])
        yield True
        
        
    def send_json(self, data):
        data = json.dumps(data)
        self.sock.send("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: %s\r\n\r\n" % len(data))
        self.sock.send(data)

    def serve_webpage(self):
        if exists(join(THIS_DIR, "index.html")):
            with open("index.html", "r") as h: page = h.read()
        else: page = html_page
        
        try:
            self.sock.send("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: %s\r\n\r\n" % len(page))
            self.sock.send(page)
        except:
            print "serving webpage", sys.exc_info()
    

    def stream_music(self, music):
        if not self._stream_gen:
            self._stream_gen = self.send_stream(music)
            done = self._stream_gen.next()
        else: done = self._stream_gen.send(music)
        return done            


    def send_stream(self, music):
        self.sock.send("HTTP/1.1 200 OK\r\n\r\n")
        
        while True:
            try: sent = self.sock.send(music)
            except socket.error, e:
                if e.errno == errno.EWOULDBLOCK:
                    pass
                else:
                    break
                
            music = (yield False)   
        yield True
        



        
class PlayerServer(object):
    def __init__(self, pandora_account):
        self.pandora_account = pandora_account
        
        
        # load our previously-saved station
        station = None
        last_station = settings.get("last_station", None)
        if last_station: station = pandora_account.stations.get(last_station, None)
        # ...or play a random one
        if not station:
            station = choice(pandora_account.stations.values())
            save_setting("last_station", station.id)
        station.play()
        
        self.to_read = set([self.pandora_account])
        self.to_write = set()
        self.to_err = set()
        self.callbacks = []
        

    def serve(self, port=7000):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('', port))
        server.listen(100)
        server.setblocking(0)
        
        self.to_read.add(server)
        last_music_read = time.time()
        shared_data = {
            "music_buffer": Queue(music_buffer_size),
            "messages": []
        }
        
        
        while True:
            read, write, err = select.select(
                self.to_read,
                self.to_write,
                self.to_err,
                0
            )
            
            for sock in read:
                if sock is server:
                    conn, addr = server.accept()
                    conn.setblocking(0)
                    
                    conn = WebConnection(MagicSocket(sock=conn), addr[0], self.pandora_account)
                    self.to_read.add(conn)
                    self.to_err.add(conn)
                    
                else:
                     sock.handle_read(self.to_read, self.to_write, self.to_err, shared_data)                    
                    
                    
            for sock in write:
                sock.handle_write(self.to_read, self.to_write, self.to_err, shared_data)


            for cb in self.callbacks: cb()
            time.sleep(.01)
            
            
            
















if __name__ == "__main__":
    logging.basicConfig(
        format="(%(process)d) %(asctime)s - %(name)s - %(levelname)s - %(message)s",
        level=logging.INFO
    )


    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-u', "--username", dest="user", help="your Pandora username (your email)")
    parser.add_option('-p', '--password', dest='password', help='your Pandora password')
    parser.add_option('-i', '--import', dest='import_html', action="store_true", default=False, help="Import index.html into pandora.py")
    parser.add_option('-e', '--export', dest='export_html', action="store_true", default=False, help="Export index.html from pandora.py")
    parser.add_option('-d', '--debug', dest='debug', action="store_true", default=False, help='debug XML to/from Pandora')
    options, args = parser.parse_args()
    
    
    # we're importing html to be embedded
    if options.import_html:
        html_file = join(THIS_DIR, "index.html")
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
        
        print html
        exit()
        
    # we're exporting the embedded html into index.html
    if options.export_html:    
        html_file = join(THIS_DIR, "index.html")
        if exists(html_file):
            logging.error("\n\n*** html NOT exported, %s already exists! ***\n\n", html_file)
            exit()
        logging.info("exporting html to %s", html_file)
        with open(html_file, "w") as h: h.write(html_page)
        exit()
        

    if not options.password or not options.user:
        parser.error("Please provide your username and password with -u and -p")

    if options.debug:
        debug_logger = logging.getLogger("debug_logger")
        debug_logger.setLevel(logging.DEBUG)
        lh = logging.FileHandler(join(gettempdir(), "pypandora_debugging.log"))
        lh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        debug_logger.addHandler(lh)

    account = Account(options.user, options.password, debug=options.debug)
    server = PlayerServer(account)
    
    webopen("http://localhost:7000")
    server.serve()
