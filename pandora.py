import time
from xml.etree import cElementTree
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


try: from urlparse import parse_qsl
except ImportError: from cgi import parse_qsl



THIS_DIR = dirname(abspath(__file__))
TEMPLATE_DIR = join(THIS_DIR, "templates")









class Connection(object):
    """
    Handles all the direct communication to Pandora's servers
    """
    _pandora_protocol_version = 32
    _pandora_host = "www.pandora.com"
    _pandora_port = 80
    _pandora_rpc_path = "/radio/xmlrpc/v%d" % _pandora_protocol_version

    def __init__(self, debug=False):
        self.debug = debug
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
        logging.info("authenticating with %s" % email)
        get = {"method": "authenticateListener"}

        authenticated = False
        authenticate_tries = 3

        while not authenticated and authenticate_tries:
            logging.info("trying to authenticate with pandora...")
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

            if self.lid: authenticated = True
            else: 
                authenticate_tries -= 1
                logging.error("failed authentication, trying %d more times" % authenticate_tries)
                time.sleep(1)

        if not authenticated:
            logging.error("can't authenticiate with pandora?!")
            raise Exception, "can't authenticate with pandora!?"

        logging.info("authenticated with pandora")




class Account(object):
    def __init__(self, email, password, mp3_cache_dir=None, debug=False):
        self.connection = Connection(debug)        
        self.email = email
        self.password = password
        self._stations = {}

        if not mp3_cache_dir: mp3_cache_dir = gettempdir()
        self.cache_dir = mp3_cache_dir

        self.current_station = None
        self.current_song = None

        self.login()

    def stop(self):
        self.current_station.stop()

    def pause(self):
        self.current_station.pause()

    def login(self):
        self.connection.sync()
        self.connection.authenticate(self.email, self.password)

    def logout(self):
        pass

    @property
    def stations(self):
        """ a private getter that puts the stations, sorted alphabetically,
        into the self.stations attribute dictionary """

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

        return self._stations




class Station(object):    
    PLAYLIST_LENGTH = 5
    PRELOAD_OFFSET = 60

    def __init__(self, account, stationId, stationIdToken, stationName, **kwargs):
        self.account = account
        self.id = stationId
        self.token = stationIdToken
        self.name = stationName
        self.current_song = None
        self._playlist = []
        
    def play(self, block=False, next_song=False, **kwargs):
        """ plays the next song in the station playlist """
        if self.account.current_station and self.account.current_station is self and not next_song:
            logging.info("%s station is already playing" % self.name)
            return self.current_song
        
        if self.account.current_station: self.account.current_station.stop()

        logging.info("playing station %s" % self.name)

        self.current_song = self.playlist.pop(0)
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
        return self.next()

    def next(self, **kwargs):
        return self.play(next_song=True, **kwargs)

    @property
    def playlist(self):
        """ a playlist getter.  each call to Pandora's station api returns maybe
        3 songs in the playlist.  so each time we access the playlist, we need
        to see if it's empty.  if it's not, return it, if it is, get more
        songs for the station playlist """

        if len(self._playlist) >= Station.PLAYLIST_LENGTH: return self._playlist

        format = "mp3-hifi" # always try to select highest quality sound
        get = {
            "method": "getFragment", "lid": self.account.connection.lid,
            "arg1": self.id, "arg2": 0, "arg3": "", "arg4": "", "arg5": format,
            "arg6": 0, "arg7": 0
        }

        got_playlist = False
        get_playlist_tries = 2

        while not got_playlist and get_playlist_tries:
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
            else:
                get_playlist_tries -= 1
                logging.error("failed to get playlist, trying %d more times" % get_playlist_tries)
                self.account.login()

        if not got_playlist: raise Exception, "can't get playlist!"

        return self._playlist

    @staticmethod
    def finish_cb__play_next(account, station, song):
        station.next(finished_cb=Station.finish_cb__play_next)

    def __repr__(self):
        return "<Station %s: \"%s\">" % (self.id, self.name)

    def __str__(self):
        return "%s" % self.name


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


class Song(object):
    bitrate = 128
    read_chunk_size = 4096
    

    def __init__(self, station, songTitle, artistSummary, audioURL, fileGain, userSeed, musicId, albumTitle, artistArtUrl, **kwargs):
        self.station = station
        self.seed = userSeed
        self.id = musicId
        self.title = songTitle
        self.album = albumTitle
        self.artist = artistSummary
        self.album_art = artistArtUrl

        self.__dict__.update(kwargs)


        self.purchase_itunes =  kwargs.get("itunesUrl", "")
        if self.purchase_itunes:
            self.purchase_itunes = urllib.unquote(parse_qsl(self.purchase_itunes)[0][1])

        self.purchase_amazon = kwargs.get("amazonUrl", "")


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

        self.filename = join(self.station.account.cache_dir, "%s-%s.mp3" % (format_title(artistSummary), format_title(songTitle)))

        self.started = None
        self.stopped = None
        self.paused = False

        self.done = False # if the song has finished playing
        self.progress = 0 # number of seconds that have passed in playing

        self._stream_gen = None
        self._sock = socket.socket()
        

    @staticmethod
    def _decrypt_url(url):
        """ decrypts the song url where the song stream can be downloaded.  the
        last 48 bytes are encrypted, so we pass those bytes to the c extension
        and then tack the decrypted value back onto the url """
        e = url[-48:]
        d = decrypt(e)
        url = url.replace(e, d)
        return url[:-8]

    def load(self, block=False):
        if block: return self._download()
        
    def fileno(self):
        return self._sock.fileno()
    
    def read(self):
        if not self._stream_gen: self._stream_gen = self._stream()
        try: data = self._stream_gen.next()
        except StopIteration: return False
        return data

    def _stream(self):
        """ downloads the song file from Pandora's servers, returning the
        filename when complete.  if the file already exists in the cache
        directory, just return that """

        logging.info("downloading %s" % self.filename)
        
        
        bytes_per_second = self.bitrate * 125.0
        sleep_amt = Song.read_chunk_size / bytes_per_second


        split = urlsplit(self.url)
        host = split.netloc
        path = split.path + "?" + split.query
        
        req_template = """GET %s HTTP/1.0\r\nHost: %s\r\nRange: bytes=%d-\r\nUser-Agent: pypandora\r\nAccept: */*\r\n\r\n"""

        def connect(start=0):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.connect((host, 80))
            sock.setblocking(0)
            sock.send(req_template % (path, host, start))            
            return sock
        
        self._sock = connect()
        
        
        try: data = self._sock.recv(1024)
        except socket.error, err:
            if err.errno is errno.EWOULDBLOCK:
                pass
            
        m = re.search("Content-Length: (\d+?)\r\n", data)
        self.song_size = int(m.group(1))        
        self.duration = self.song_size / bytes_per_second
        
        yield data[data.find("\r\n\r\n")+4:]

        mp3_data = []
        byte_counter = 0
        last_read = 0
        while True:
            now = time.time()
            if now - last_read < sleep_amt:
                yield None
                continue
            
            last_read = now
            try: chunk = self._sock.recv(Song.read_chunk_size)
            except socket.error, err:
                if err.errno is errno.EWOULDBLOCK:
                    print "blocking"
                    yield None
                    continue
            else:
                if chunk:
                    byte_counter += len(chunk)
                    mp3_data.append(chunk)
                    yield chunk
                    
                # either the song is done, or we got disconnected.  check
                # for both
                else:
                    if byte_counter == self.song_size: break
                    else: self._sock = connect(byte_counter)
            
        mp3_data = "".join(mp3_data)
        
        

        # tag it
        tag = ID3Tag()
        tag.add_id(self.id)
        tag.add_title(self.title)
        tag.add_album(self.album)
        tag.add_artist(self.artist)
        # can't get this working...
        #tag.add_image(self.album_art)

        # write it
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
        logging.info("liking %s" % self)
        self._add_feedback(like=True)

    def dislike(self, **kwargs):
        _pandora.stop()
        logging.info("disliking %s" % self)
        self._add_feedback(like=False)
        return self.station.next(**kwargs)

    def __str__(self):
        minutes = int(math.floor(float(self.length) / 60))
        seconds = int(self.length - (minutes * 60))
        return "\"%s\" by %s (%d:%02d) (%+.2f)" % (self.title, self.artist, minutes, seconds, self.gain)

    def __repr__(self):
        return "<Song \"%s\" by \"%s\">" % (self.title, self.artist)



















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










out_key_p = [
    0xD8A1A847, 0xBCDA04F4, 0x54684D7B, 0xCDFD2D53, 0xADAD96BA, 0x83F7C7D2,
    0x97A48912, 0xA9D594AD, 0x6B4F3733, 0x0657C13E, 0xFCAE0687, 0x700858E4,
    0x34601911, 0x2A9DC589, 0xE3D08D11, 0x29B2D6AB, 0xC9657084, 0xFB5B9AF0
]

out_key_s = [[
    0x4EE44D9D, 0xCCEEAB0F, 0xD86488F6, 0x25FDD9B7, 0xB0DE3A97, 0x66EADF2F,
    0xC0D3DCA4, 0xEE72A5FA, 0x54074DEC, 0xCBAD83AD, 0x4B1771A3, 0xD92AE545,
    0xB5FCE937, 0x26AD96D9, 0x5D615D68, 0xF2994B82, 0xE668D342, 0x61051D4C,
    0xCFB29CA4, 0x8B421D38, 0xDA3B4EB9, 0xD92D6A55, 0xF7D940C7, 0x99C4BC83,
    0xAB896E79, 0x77C7039B, 0x1215B24A, 0x0C0EBC0D, 0xE9F082B2, 0x6B7DFE9C,
    0x4A714E76, 0x91280D88, 0xA422A361, 0x3E674D4A, 0x6EBC2D42, 0x6838580B,
    0xBAE461AB, 0xE8FEDD17, 0xEFD6E5E0, 0x690D3E93, 0x32FADEB0, 0x1B99EE04,
    0xBE9FA7D9, 0x7997DFC6, 0xFD1B8025, 0x667B35D8, 0x2D909996, 0xFE487FF0,
    0x628BCFE1, 0xA534C620, 0x6644DEFE, 0x8BF9236D, 0xE943DD51, 0xF4615657,
    0x605D4F80, 0x2E02FC45, 0xD924D2D0, 0xFD4AB9E3, 0x5AEB18F0, 0x7A8D7C92,
    0x6CA40CA6, 0xD8AD4139, 0xCA5E7EC2, 0x69BE3C59, 0x554A4DD6, 0xBA474DD1,
    0xE113576B, 0xCB89A6BD, 0xF366EC0C, 0x876661AB, 0xD85E5381, 0x79A93327,
    0x5A4E5D92, 0xE3301F23, 0xF211DD61, 0x6F0140D0, 0xDBA134BF, 0x3C623008,
    0xD5FCE976, 0x6EDE648E, 0x814CF920, 0xB38878E1, 0x6232D49C, 0x2310373B,
    0xA8C6EBFC, 0xCD506842, 0x62BEF441, 0x1324C803, 0x69D1F137, 0x3907EE67,
    0x47967932, 0xC3C3F280, 0xC4B036B9, 0x5EC264B4, 0x9484AA3C, 0x5FEF9C53,
    0xC1B9030F, 0xE86C6BBA, 0x3AE49DAE, 0xBBAC421C, 0x54D06D99, 0xBA13A2B2,
    0x3132FA87, 0x2FDDB5E2, 0x4B751219, 0x5B59778F, 0xEFFA2E62, 0x3BD56164,
    0xE7EDFC1D, 0xCF4D5FDB, 0xC6310BDA, 0x0CAE8B8F, 0x53196C2F, 0xAC951D1F,
    0x32FD1D71, 0x7D9D5956, 0x2EA62C92, 0x9FA4A4C8, 0xE491DC41, 0x7E5F2507,
    0x4568300F, 0xF210AAA8, 0xB6980949, 0x017405E7, 0x5EBF3350, 0x44B863F6,
    0xDF96854A, 0xFA8A8390, 0x342BDFFA, 0x93096350, 0xCD0F0083, 0xBE295FDD,
    0x549AA9C9, 0x8554D31B, 0x2F2FE138, 0x30E8C78D, 0xED603733, 0x4B47F4C2,
    0x03D481DC, 0x8BE4479C, 0x9A307E98, 0x73CFC5DC, 0x71DE3DFB, 0x55DA2605,
    0x2CC97898, 0x13F0CC6F, 0x5F30FEE1, 0xF65D36D0, 0x99D05438, 0xB6A1DF23,
    0x2EA6EF9B, 0x12D3A110, 0xF1C89B1A, 0x522BAA1F, 0xE39AC7B3, 0xAFC153D1,
    0x2A565274, 0x51530B46, 0x1291387D, 0x15BC2B63, 0xA73AD01F, 0x13EBC4A7,
    0x849583D7, 0x4A9E1AE6, 0x430C9A05, 0xEB2A78FB, 0xFA3A817D, 0x6D1D7AE5,
    0xB99588F5, 0x6D2C571B, 0xF975441C, 0x1348927D, 0xB069BDE2, 0x0771A398,
    0x4B93EDCC, 0x3C167223, 0xC3BBCFDF, 0x40C406DA, 0x81C867B1, 0xEB20C3D2,
    0x2476ED54, 0xB581F042, 0x1160A8B8, 0xBCA1AD0F, 0xD8F18C9F, 0x708BC7C6,
    0x0579D83C, 0x29BAA2B8, 0x45B845EE, 0xA57F5579, 0xE52E4A8A, 0x48365478,
    0xC6CCBFB4, 0x2F53D280, 0x8E1FF972, 0xF4E02067, 0x3F878869, 0x5879FF3C,
    0x1EDFAB0F, 0xD4FE52E3, 0x630AC960, 0xABD69092, 0xFAA3BF43, 0xF1CA3317,
    0x9CFF48D2, 0x8FE33F83, 0x260C1DE3, 0x89DB0B0B, 0xF127E1E3, 0x7DA503FF,
    0x01C9A821, 0x30573A67, 0x8A567A2E, 0xE47B86CF, 0xB8709ADE, 0xB19ADD3A,
    0x46A37074, 0x134CE184, 0x1F73191B, 0xE22B39F6, 0xE9D35D3D, 0x996390AF,
    0xADBBCCDB, 0xC9312071, 0xD442107D, 0x0B50C70A, 0xB9B6CC8C, 0x60A51E0E,
    0xA1076443, 0x215F1292, 0x5A53C644, 0xEA96EA2E, 0xE9F3B4BC, 0xBA5F45D2,
    0x454B65D6, 0x2CF04D9C, 0x05EF1D0F, 0xCD1ABBEE, 0xE86697B0, 0xFB92F164,
    0xEBEDADBF, 0x69282B8D, 0x65C91F0D, 0x6215AB51, 0x87E7BDF6, 0xC663D502,
    0x6EF4864E, 0xDC3BDCC9, 0x97184DBB, 0xCD315EED, 0x64001E09, 0x6F7DE8CE,
    0x38435D03, 0x840B5C82, 0x23CDBC8A, 0x7FA0D4FB
    ], [
    0xEBCBE20D, 0x09FADAEC, 0x98FF9F63, 0x16D0DFE1, 0x54B65FA8, 0x8C58D07C,
    0xEAACBEA0, 0xEA8BC5B7, 0xD343B8ED, 0x46D416FC, 0x0247DCBB, 0x527CA3F5,
    0x22DAF183, 0x6684CF7F, 0xA2D5D9F6, 0xC507E43B, 0x7B368AE6, 0xFC8179EC,
    0x47E959C4, 0xDADF15F2, 0x92E48145, 0xD9CFA8B3, 0x94F209E8, 0x10F93D6D,
    0x3BAAF7B5, 0x9E5009B4, 0xE7E66FD8, 0x10F6D58F, 0x1EAFFF4D, 0x0423FCE5,
    0xE860C60A, 0x7713B2B4, 0x7C5EEF7E, 0x430801CF, 0x46613A77, 0xFADEC916,
    0x58AB09B3, 0xEE05C51F, 0xD4C6331F, 0x9BCA1941, 0x15BF041F, 0xC3B04E8D,
    0x6CD037AF, 0x11C81E53, 0xB38393DF, 0xB1D07B52, 0x067D02F7, 0xA9E5798B,
    0x4E5C10A6, 0x790DD862, 0xDEA21AD1, 0x3C0C90BF, 0xB05D8240, 0xFEA81F59,
    0x832F19FF, 0x17190D1C, 0x03E07FDC, 0x43A6AEAC, 0xFE0C8A2E, 0x216813A6,
    0xF0428728, 0xC1D21DCF, 0x54109ACB, 0x68FB51BB, 0x3F5AEE69, 0x557FEA14,
    0x07965E16, 0x58E2A204, 0x6E765B0C, 0x3B8D920F, 0xDD712180, 0xDD0F67CA,
    0x37F9D475, 0x91815CCF, 0xC31A34BB, 0x8F710EF2, 0xF2DA2F82, 0x2A24931B,
    0x41CFF29F, 0x16C9BECF, 0x1AEB93FB, 0x090DF533, 0xC10D27B6, 0xF7EE2303,
    0xF82A0ED0, 0x57031132, 0x88AFF451, 0x574A8BFF, 0xF1ACA4F0, 0xDD556F49,
    0x90D7CF52, 0x4BCA4AA3, 0xC917557C, 0x4BB6B151, 0x52CD8251, 0x7C7ED836,
    0x3488ED59, 0xC50C6A0B, 0x675413ED, 0x6368583D, 0x98B61BAE, 0x1AF59261,
    0x46590022, 0xA4C70187, 0x4658F3EB, 0x80A61049, 0x8F120E7A, 0xBEAC09D8,
    0x195ACD49, 0x6BE1DE45, 0x6EF1E32D, 0xB8A4B816, 0xC18758B8, 0xCA7AD046,
    0xD475BFE1, 0xCC3AB8AF, 0x45AB9AD7, 0xC37C62AC, 0x9AAD7E2E, 0xB9D87862,
    0x28F3CD26, 0xA0577A0E, 0x75859ECE, 0x4A6E5B86, 0xE61E36B3, 0xA00E0CA4,
    0x3E2CC99C, 0x581DF442, 0xCE40B79B, 0x17BAB635, 0x73F1C282, 0x7C009CE0,
    0x1A8BBC5A, 0xBBB87ECD, 0x162ED0AC, 0x8DB76F5A, 0xD5AD1234, 0xD0D7A773,
    0x41CBDEFB, 0x7197AFF4, 0x5C60E777, 0x5D9141D4, 0xF43D5211, 0xA4F064D9,
    0x40C13CB3, 0xE9DE900D, 0xBF733203, 0xC00F2E89, 0x095D476F, 0x277A825D,
    0x4B6A61D3, 0xFF857740, 0xE34705C0, 0x65F8372C, 0x497AC161, 0x1231CA4A,
    0xFB385036, 0x24B36150, 0x6CB9FA2D, 0xCBAB3399, 0x3832629E, 0x1BB815EE,
    0x6AAA74C7, 0x8FFA22B8, 0x64093F28, 0x973BBA95, 0x831A8195, 0x48B2923D,
    0x9680C36E, 0x16BA5344, 0x1F190542, 0xBCB0DFCC, 0xCCC24623, 0xFA503EAD,
    0x7189956C, 0x80B3C715, 0xFA9F4685, 0x36CF833E, 0x19A53ADF, 0xA5A4BD79,
    0x187ADC8D, 0x8AEFA6B6, 0xF64FF62A, 0x88A590BA, 0xE30C75BE, 0xA3BFBCC7,
    0xAC669722, 0xC4AEAFF2, 0x822DC5FA, 0xAA73C1D5, 0x422EFD93, 0x946FE915,
    0xEF623E46, 0x24395A31, 0xF28FF488, 0xB4D7CA7E, 0x27703504, 0x9F390B73,
    0xA6999558, 0x8AE04A20, 0xDD6FE7DB, 0x55963137, 0xCFEF70BB, 0x708CA677,
    0x804CF78B, 0xD5AC1CA2, 0x88D7CCFC, 0x5FE056DF, 0x25B390EA, 0x11550845,
    0x15A58C0B, 0x7C3530A3, 0x24550544, 0xD395EDD0, 0xEB046782, 0x7E3CCE71,
    0x25A8640C, 0x96A955DE, 0x4BF7614E, 0x3014FD08, 0xE2AC1E2E, 0x7D3AB3C3,
    0xB63CB59C, 0x9E92D401, 0x859B2C44, 0x1F893940, 0xEE81B9BB, 0x7F430589,
    0xAF2CC2EC, 0x0FA273E2, 0x3E5C6FAA, 0xE580E6A9, 0x64D73FE6, 0xE7C5A28A,
    0x99B760BC, 0xC0FCBA71, 0xDB521C76, 0xDBC7C1F8, 0x4968CF63, 0xD4928D17,
    0x6DBBCC5F, 0x681EB668, 0xC326CEB9, 0x7C6B0EBB, 0xF071C193, 0x5CC6A08C,
    0xFA4B95EB, 0x0BED345D, 0x16854F61, 0x22ECDDA9, 0x77335F2D, 0xCC016EE5,
    0x4CE1D7F6, 0x32B1409B, 0x2197B046, 0x73CD94F3
    ], [
    0x56D997EE, 0x92FA3097, 0xA1AF0D9D, 0x11FCBB9C, 0xA2673993, 0x3860F1CE,
    0xB2B70A39, 0x5BC90183, 0xBFA62ADC, 0x58E257F2, 0xD221A704, 0x0A876CE4,
    0xD7B0FCA9, 0x80D3D874, 0x696A6CFD, 0xB989EFF1, 0xEAA5F132, 0xA29ECB5D,
    0x674B7380, 0x0BAD725F, 0x59D55508, 0x8DB40E2A, 0x003EBD12, 0x871AD00E,
    0x7ACE20A9, 0xE670BA85, 0x43D53997, 0x79461049, 0x806C102B, 0xB21337BD,
    0x791483E8, 0x6ECA44EA, 0x959CF50D, 0x8D87166D, 0xFA939DF8, 0xB0E519DE,
    0x8C069B44, 0x0A47F71A, 0x8D7AD1CA, 0x24E6FEDD, 0xCEF2173E, 0xB46A57F1,
    0x9DD9C775, 0x549B2E5D, 0x67A37485, 0x38F7FC18, 0xA269F5A1, 0x1B04F14E,
    0x4550E006, 0x8F5E0E14, 0x5EB9992C, 0x88D780A5, 0x334FFA1E, 0x473A75C1,
    0x9D96E913, 0x7DB16188, 0xE699B708, 0x88D087FA, 0x06E44D4E, 0xCB29E519,
    0x68529AB8, 0xBC74B1FD, 0xDA074140, 0x557B9936, 0x80BB557E, 0x42522D24,
    0x909E967F, 0x7D578A28, 0x7F78EBD7, 0xB793DC4B, 0x08498F07, 0x8A77FC08,
    0xFFFDA0C1, 0x2ECA4123, 0xB63861DC, 0xD909606E, 0x29A545E4, 0xB37539D6,
    0x292FAC93, 0xBDC6C4F3, 0xDAC7CE05, 0x68201C9D, 0xE08DC67A, 0xE0FB0327,
    0x17554D62, 0x636D9040, 0x0612D29F, 0xAF250475, 0xB8961740, 0xBE3E4408,
    0x3AF166E6, 0x3B16CC87, 0x2DC77141, 0x3C874024, 0x0E409623, 0xC7576B7A,
    0x35CAF7DA, 0x0AA9AED6, 0x6C5F2CC0, 0x23AAB90F, 0x74A41C51, 0xDAA1B557,
    0x412EC422, 0xD9E55CF0, 0x7F6A804E, 0x9256A133, 0xF3FD2639, 0x42C9A68A,
    0xB20588E4, 0x33339C04, 0xCB9B9300, 0xCCA198E9, 0x849A2FFF, 0xF2B71118,
    0xD27C41DF, 0xF1453CD9, 0xEB94D640, 0x9CE6A69E, 0x1561C1BD, 0x8A8F7E07,
    0x1FA3989C, 0x601C3440, 0x95DE5ED8, 0xB2F2AE94, 0x831BA7C3, 0x6831E3ED,
    0x5C5C0BD8, 0x628A0E89, 0x2726D7A3, 0x82B6E434, 0xB729A5C7, 0x5AB563C2,
    0xA4119CE6, 0x4459E404, 0x0B3E858A, 0x080C2DF9, 0x6EBE3FFB, 0xC1D64BCE,
    0xB2C90336, 0x998AE507, 0xC152879A, 0x31B99F23, 0x37769978, 0xF5C78668,
    0x2B954114, 0x54169F1A, 0xBF9E6E7D, 0x41BEBC39, 0x35BC63BD, 0x77E91F12,
    0x89909690, 0xCB17B79D, 0xCCBF4A25, 0x3E5E653E, 0x3B4531F1, 0x31AF6109,
    0x027DC03F, 0x334AE2A7, 0x8A685A70, 0xD82C335D, 0x7D73C193, 0xF0311C79,
    0xE8091EAF, 0x64B12983, 0x85CEB9A6, 0x402AB7C9, 0xA95E4546, 0x85CE4FD7,
    0x21968004, 0x0846E117, 0xD290B888, 0xCE2888FC, 0xE2F318F1, 0x89B189DD,
    0x7A2D73BA, 0xE28937E5, 0x6D857435, 0x8A2F05FA, 0xA19B966F, 0x37EF297F,
    0xC50696F5, 0xA7C3DE1A, 0x988D3850, 0x24007793, 0xB94C792C, 0x4DA98736,
    0xA04EB570, 0x4AA44F84, 0x7124E7C6, 0x13B9026E, 0x27AC2D15, 0xFBB9AD93,
    0x2F94AA1C, 0x98587A3D, 0x9C9DB996, 0x7E3487D5, 0xA819272C, 0x32AA5E43,
    0xE0DB72F5, 0x4DB4853C, 0x7350C7EC, 0xB1626C73, 0x07130A5F, 0xC3DAA529,
    0xD6422735, 0x8559200D, 0x1046E85C, 0x326CFB54, 0xAD42DB6A, 0xAE4CC364,
    0xA49F5718, 0xF472F8A0, 0x3C002484, 0x013067BE, 0xC88A1317, 0x4C3C209B,
    0x7CBB8BB3, 0x41FB8DAF, 0x236591B3, 0xDC974A45, 0x8639E738, 0x97C38B19,
    0xD7FF5725, 0xE7094458, 0xF28B223F, 0xF73C878B, 0x7F7502D9, 0x52F7FD09,
    0x4A661B36, 0x62814D8E, 0xBBDD1D16, 0x002598D9, 0x56B17A84, 0x87A331B7,
    0x6C2898C2, 0xAFCBA795, 0x4EFEE9AE, 0xEAE3A4F1, 0xC3D4D9CD, 0x5EFD7C32,
    0xB1B31E64, 0x95245686, 0x21A7DA12, 0x7155E041, 0x7362B475, 0x36486BD5,
    0xA97E5D7C, 0x8871303B, 0x93199D52, 0x246F919E, 0x5A581359, 0x6AE746DD,
    0x3CA9098C, 0x56DA5714, 0xAA0B674A, 0x08C89A5D
    ], [
    0x7DD47329, 0xF270A704, 0x71BF31DA, 0x3B57772E, 0xFBE90F4B, 0x87FC23F6,
    0xCF413D71, 0x4FFEA8EC, 0xEFBA20C2, 0xEB53E0C1, 0xFFE7633E, 0x854E28E8,
    0xFBFFE904, 0x8A7841BE, 0x94E99960, 0xA3E69064, 0x365C57AB, 0xBEE976CC,
    0x596B94C2, 0x8C5E90E2, 0x074B3C54, 0x89B5E926, 0xDF192C71, 0xAF631D85,
    0x67A8EDEC, 0x24BE4919, 0x81EB9C8A, 0xFDB13471, 0xEE61A4A1, 0x1EE368DE,
    0x8C55C255, 0xD273A000, 0x12A24DCD, 0x22A6708E, 0x6BB4C19A, 0xF2599FDE,
    0xE84B8A95, 0xDD578159, 0x1F666F1E, 0x483BBCE2, 0x46E340BA, 0x8B7D6490,
    0xE65BD77D, 0xA50F2282, 0x4B455D23, 0x9B5D486B, 0x95CEA1A3, 0x4B7A484A,
    0x2E16BE82, 0x096A8E05, 0x5494AF5E, 0x1EBA1525, 0x84FDB773, 0xD47CE143,
    0xC1254007, 0x1CE4CBBE, 0x8049402D, 0x114D7B59, 0x64D760AD, 0x6AEECE49,
    0x83DC9867, 0x36FF9C28, 0x6FFB709D, 0xB22F7301, 0x6E6CAD92, 0x0001F394,
    0xB560CDE7, 0xEA02FDDA, 0x40609266, 0x7F599B81, 0x1B8FD59A, 0xA562FF5C,
    0xA01750C6, 0x78A35114, 0x789F8094, 0xF46594B8, 0xFF3A12BE, 0x29DDEB50,
    0xE3CF5A2C, 0x8E440B20, 0xBFBF3DD8, 0x649DB58A, 0xC48A8A51, 0x97F139C3,
    0x0BB07943, 0x548C90BD, 0x8153FCF1, 0x13098DEF, 0x812EA492, 0xFC0AC487,
    0xC5EAE50A, 0x7A02481B, 0xC75279D7, 0x59CBC149, 0x6AB39416, 0x39331E1A,
    0x233BE50B, 0x7F09C1BD, 0xECC11E6E, 0xA6647D03, 0x06BD33AD, 0xD717C795,
    0xE07E2D67, 0x2688D40B, 0xE23E349F, 0x8C7F559E, 0x3BA698C2, 0xEB5FCD3C,
    0xE94E2DE5, 0x3C0FE4DF, 0x55454456, 0x12731019, 0x21AF58D7, 0x2555CE03,
    0x17BBC647, 0xF0C66012, 0xE02D87F8, 0x340DB0CE, 0x72A3766F, 0xE2724C51,
    0x3636A5FD, 0xC226C419, 0x1A5F0464, 0xA543817B, 0x0B850A8D, 0xD5A6F88B,
    0xCE3715B8, 0xB73918A2, 0x6AC92E61, 0x0FCD43EA, 0xF559EEDE, 0x3482C340,
    0x447D9924, 0xF95D6EB2, 0xB22E6C6F, 0x935740D2, 0x7C04B228, 0xB90ABD1A,
    0x8D9D01C9, 0x43B63B2D, 0xE0EBEDAC, 0x7C219604, 0x8479756F, 0xB67355FE,
    0xA056539B, 0xAF1D5A02, 0x6660BB07, 0xD1A0593C, 0x5AABEF47, 0x73802FC5,
    0xAADB5251, 0x92556CFF, 0x5BF44BDC, 0x4DC171CF, 0x1EE4E879, 0x516BC896,
    0xCDBB21EA, 0xF513BD04, 0x94267720, 0x6B29DAC1, 0x1D778D67, 0x9625EA42,
    0x23946BBC, 0xF23D2E0A, 0x001C2CFB, 0xEF121203, 0x963A0C2B, 0x1AAE960B,
    0x13F2D588, 0xAE6BFEAE, 0x77424AC8, 0x1E0B2A9F, 0x9074C626, 0x9BCDE764,
    0xF8539561, 0xC14A5B05, 0xD88D9FAE, 0x2C5C4C67, 0x2C63BAE5, 0x99CCF4CB,
    0x3563CA53, 0x0CE7A114, 0xCB8938D3, 0x7C61537F, 0xE717A35E, 0xB69D3832,
    0xE47931C3, 0xD5C9D409, 0x355E0B97, 0xC60EB27E, 0xB17978F6, 0x77CCBCEA,
    0x85AEFA12, 0x59DFA376, 0x36DB61D2, 0x96832915, 0xCC4411F3, 0xB81F1EF9,
    0x2C54E5E1, 0xDD3CE944, 0x02D92E29, 0x1D4795B1, 0x27F900B0, 0x97A516CC,
    0xA2DB2CC8, 0x3125B863, 0xBF44DC77, 0x211A0226, 0x3A98AB5F, 0x2612396E,
    0xA1BEF080, 0x708B7433, 0x5D457230, 0xED03C4EB, 0xA84D73AE, 0x89D5582D,
    0x95F0C7FA, 0xEF51B8C9, 0xF9DCA97D, 0xCB2E49FD, 0xC12B4ADD, 0x611C9AD5,
    0x35D1D7CE, 0xA77E13BE, 0x207C1B88, 0x0AC289D4, 0x4B553B81, 0x4940991A,
    0x23D9F9D5, 0xDFD93925, 0xB924E9D2, 0xBFA61D10, 0x861FDF0F, 0xBBD30811,
    0x953CE5DA, 0x92B48334, 0x5E5B44FC, 0x5B949533, 0x31A5D165, 0x99339641,
    0x2737671F, 0x512EB25C, 0x54408346, 0xA090A7FE, 0x1D9CA5F9, 0x470C19E4,
    0x720F936E, 0xA8628453, 0x364D29CC, 0x42E472DF, 0x54949196, 0x6C7C46EA,
    0x12797418, 0x7D775295, 0xC46A7C32, 0x69CE8560
]]



in_key_p = [
    0x71207091, 0x64EC5FDF, 0xA519DC17, 0x19146AB7, 0x18DF87E7, 0x98377B97,
    0x032887B7, 0xC7A310D5, 0xA506E589, 0xE97346B9, 0xE3AA5B39, 0x0261BB1D,
    0x466DDC6D, 0xDEF661FF, 0xCD257710, 0xE50A5901, 0x191CFE2E, 0x16AF68DD
]

in_key_s = [[
    0x53B04195, 0x34D8664F, 0x564FA6F9, 0x943A4261, 0x43EE3112, 0x2FBC9B61,
    0x31C92B2F, 0x5F23E485, 0x1D51F5AE, 0x45589853, 0xEC79BEF5, 0x64E56904,
    0xD2B94FB8, 0x12ECF786, 0x39FBB15C, 0xADC822DF, 0xF63EB8D0, 0x707E6E03,
    0xC9EE963B, 0xAF4C533B, 0xB6CA295D, 0x669EC8F6, 0x5B2128DA, 0xCDCC7042,
    0xECE8EA68, 0xB6564227, 0x042D1DE9, 0xA7BB4D38, 0x702AF25D, 0x04218195,
    0xDA5DBB60, 0x05776188, 0xE6FB54D4, 0xD0D273F3, 0xF498395C, 0xD2FFAA63,
    0x2ECC5F00, 0x9B785AAB, 0xE88DF10E, 0x46A5C5A7, 0xCB05BAF6, 0x93D07466,
    0xFD82EB2F, 0x29C7525C, 0x88151216, 0x7FEA6803, 0x79AF1507, 0xABEEF999,
    0x2C338D91, 0x8BDC112D, 0xAE639DF1, 0x3395771D, 0xE5D05978, 0x985DFBCE,
    0x7A33712C, 0x77EE0800, 0x6A3235A7, 0xAD25178A, 0x5DEAB99C, 0xA518351E,
    0xE191C29C, 0x8F58F7B5, 0x8F59301A, 0x230D7717, 0x36480929, 0xE7389FA5,
    0x75101209, 0xCC80D6EA, 0x20A77201, 0xDF34CC7F, 0xDD0D15EC, 0xAAD39BD7,
    0xE148C1C8, 0x60053D1E, 0xA765BBAA, 0x055A807E, 0x243EF591, 0x3BC6A318,
    0x86B4E2A8, 0x36068D06, 0x8E38E7ED, 0xF6EF1C43, 0x4BC12D1C, 0xEE5CD4DE,
    0xA5635E1F, 0x4EA7103F, 0xE37CC2D5, 0xAA24D152, 0xC10D25A1, 0xB5A4B1DD,
    0x38A0E8AF, 0xC2E96D92, 0xC537DB8C, 0xFB00FD39, 0x96D3E31F, 0x1587D1D5,
    0x3D3C6162, 0x116E9A21, 0x5E73A15A, 0xFB1207F6, 0x205C8CE6, 0xCD2EB8F4,
    0xBF2D8E53, 0xC00799B6, 0x05AB657A, 0x5A337660, 0x13F66753, 0x769969FF,
    0x112E9892, 0xE900968F, 0xC09E5462, 0xF83D1DCD, 0xCE8730EE, 0xB8A9F537,
    0x7D4A07F9, 0xE885BB2A, 0x6CAE2932, 0x959FF20B, 0x266FF5A7, 0xD2465D75,
    0x20BA50CD, 0x3DADB44A, 0xE72D39EE, 0x1B3D759E, 0x4F537258, 0x0F403950,
    0xE7F64B2C, 0xE033D524, 0x07FF6009, 0x2C8270B2, 0x6AA43085, 0x56659DE5,
    0x2082EA85, 0x3D347FAB, 0x2C903DF5, 0xE7B54B39, 0xAAD7B6F3, 0x10BDF5DF,
    0x9F8405F8, 0x74635701, 0xBBC945A3, 0xEF0F67C2, 0x8ECFE353, 0xC47947D4,
    0xCB638932, 0xE0DFD27D, 0x390ECFF3, 0x329941FF, 0xB7B8B2E1, 0x96129843,
    0x6D487C00, 0xF7A31803, 0xEFD5F48D, 0x144881D4, 0x82C99F97, 0x3CA6233C,
    0x5D81D880, 0x5452C86E, 0x92F7424B, 0x1ABF8230, 0x2B9D844E, 0x53151082,
    0xFFDB3720, 0x5398D8CE, 0xD2B1DB66, 0x637FEEDF, 0x0C281873, 0x0D5B080F,
    0x1CC84819, 0xA9C6416E, 0x0CBD6FA2, 0x5D311F65, 0x1B10E4AA, 0x862EFCE7,
    0xB82B8EA1, 0x2C6FCB4D, 0x48197672, 0x4DE9F5A1, 0x189A1587, 0x11B82778,
    0xDF0620A2, 0x8F9EB547, 0x0C42BC08, 0xEF386B9B, 0x2882BA91, 0x5CB16824,
    0x95B04228, 0x0A84C744, 0x4A4F12F1, 0x3328121D, 0x099D0C58, 0x9FFE4330,
    0x53676878, 0x0F4BFE6D, 0xF7F6757A, 0x849E6A1F, 0xE7F305AF, 0xABE5CF0A,
    0xD4C73D1F, 0xEC1BA28A, 0xDF481C60, 0x3A0169E9, 0x644F5F06, 0x74A03899,
    0x2E1CC42A, 0xBF181E7A, 0xBFD031DF, 0xA8F9BFAD, 0xB08FF9BC, 0x07F040A6,
    0x9AA8240D, 0x936730A6, 0x4B659CAE, 0x70792DFF, 0x50738D93, 0x2E02F9DF,
    0x01F6AA81, 0xAA5557E6, 0xF5DF588E, 0x70D3217B, 0xBFD2CF2C, 0x6193A8BC,
    0x4C1D0DAD, 0x8E626F01, 0x878B8F70, 0x422B5FEC, 0x77A5D368, 0x9C5A4A84,
    0x31260B03, 0xA00A2738, 0xFE1A531C, 0x8D268013, 0x926D9087, 0x66CCC472,
    0xA0E6BC5D, 0x72B4806C, 0xD75EC86E, 0xE2AA9D6C, 0x5F8DD394, 0x70C92981,
    0x32578C75, 0x2E110E4F, 0x20F0883F, 0x505740F5, 0xD52B76F7, 0x4C087E4F,
    0x6D6455EE, 0x2E9E895F, 0xD826A8C3, 0x032152E6, 0xE3BCD79D, 0xBA6796AA,
    0xE1F2CC8D, 0x61A70735, 0x581A8A85, 0xFF4A937F
    ], [
    0xCBD350B6, 0x04217557, 0x0B48BEDE, 0x87D87806, 0xE78282F6, 0x1518E761,
    0xD0242D75, 0xFEE8A656, 0xE1EF119C, 0x465D0A5F, 0x8627A798, 0xB2589459,
    0x6A8BF4D2, 0xAEF2E605, 0x22354462, 0xA3B5DE00, 0xC40086BE, 0xAB4FA4FA,
    0xD7C782A4, 0x095003F7, 0x76550A91, 0x51D742A6, 0xE6B1868C, 0x7A2E891B,
    0x32C24C32, 0xB71EB54E, 0x58F1F230, 0x2C73427B, 0x6CAF2DB1, 0x6E65848B,
    0x202FCC18, 0x782E5C7A, 0xCC37A03B, 0xE1D9FD4D, 0xBA8CEBE8, 0xBA5D7E12,
    0xE37E60F3, 0x99CA41AE, 0xB70F141A, 0x3399E5E6, 0x6F168416, 0xD9FCCDFD,
    0xE0149EF0, 0x6632458E, 0x192C72C2, 0xBB37A8D4, 0x4DEB6CFA, 0x1D65E9BE,
    0x9F47349D, 0xB44857CC, 0xEE4EBB41, 0x5571F80A, 0x7060121A, 0x1863AAE5,
    0x89E44DA0, 0xA8AB709B, 0xC8B0D49E, 0x7A0A4DAD, 0x53BF4EBD, 0xF52C4C3C,
    0x13E00197, 0xF5C748EA, 0x01993E7A, 0xCBEDF34F, 0xC5A6B7BC, 0xCEF7AFD4,
    0xFF554458, 0xB381FB5F, 0x13B4B32C, 0x4E7E2A88, 0x5015434F, 0x977B5E72,
    0x595037D5, 0xAA9914EE, 0xE48ECD4A, 0xB5933128, 0x819BC797, 0x1FDA1451,
    0x7E246FD0, 0x70AE3F82, 0x3ABEBFE4, 0xE89BA94A, 0x0F8BA57F, 0xAD2EDFD1,
    0x71C248D1, 0xDE31588B, 0x9058ACB8, 0x1D811B61, 0x6A459746, 0x75698B77,
    0x06C5291A, 0xC4FDC707, 0x3412F7A2, 0xA11D2CB7, 0x771C35D9, 0xFB5252B9,
    0x8ADCC6BB, 0xACFDB11F, 0xA53D102E, 0x3BDD6B7A, 0x7242DFBC, 0x600EAF36,
    0x55399BD7, 0x5C52D902, 0xE6D5A548, 0xE3599A58, 0xE98182B0, 0x748C0C18,
    0x4B2BEE05, 0xCC531DD8, 0xA3231F8C, 0x8FD29390, 0xFD8C43FB, 0x7E221049,
    0xCD489DEF, 0x2312C991, 0x55633208, 0x3514163F, 0xAB3BEE59, 0x77FFAA7B,
    0x90915A4C, 0x213FF94A, 0x3CCD3F06, 0x574DF4E9, 0x256EE273, 0xB9FEE691,
    0x979A0F80, 0xFBA4876D, 0xCA3448B8, 0x9A05788E, 0x01817985, 0xFDC95285,
    0x64FDABDD, 0x7E8EFF2E, 0xC9F8DFB0, 0x3585290F, 0xA359E9CD, 0xE7361ACE,
    0x0F05DE97, 0xA84A949F, 0x816F79D1, 0x80053F79, 0xF3ED1531, 0x0077675E,
    0xAA407D1C, 0xA905EC4E, 0xB5031A49, 0xF7840308, 0x1749068C, 0xE7B994B4,
    0x7963F746, 0xF8D6832E, 0xF2C43B73, 0x0C858859, 0x8ACA9FDF, 0xA980B080,
    0x2DA83BA8, 0x88A9E6FE, 0xA1D65DCA, 0xB7466978, 0x1C7579D5, 0xA3E829E7,
    0xA038A762, 0x2E6CFC22, 0x80A3C2DD, 0x6FB505A2, 0x40A809C9, 0x45A1345A,
    0xCA1FED04, 0x623F44F9, 0xBCECFD8C, 0xBC1EA3D8, 0x3CFA9C4E, 0xC3F84B71,
    0x245EADC3, 0x0635934D, 0xFD115EF2, 0xE6A29E9D, 0x3B135A32, 0x54316287,
    0x6427B2DD, 0x9A58AD8A, 0x18C4F685, 0x0BCF5559, 0x1F937C1C, 0xF7EBADE2,
    0xBB6D1EFC, 0x5EC3076E, 0xB330C228, 0xFB630B27, 0xBA582D2D, 0x6810A8D0,
    0x93426874, 0x2CF4EB4D, 0xBC650CDD, 0x6DE2A493, 0x7FE6B0FB, 0xF251E5DB,
    0x6F12D6BE, 0xC6BA7485, 0x5F419C45, 0x22B0F07C, 0x92EDDB3C, 0xF169D257,
    0x32663AE2, 0x04B74EA2, 0xC8A37378, 0x0652BC72, 0xB402FDC7, 0xDF7F8268,
    0x44030F03, 0xAF3FD392, 0x5491C194, 0xB85DB9CE, 0xD651CA8F, 0x1255050C,
    0xC29846CA, 0x4C839D32, 0x3F5B7E14, 0x2A877586, 0xF98A241E, 0x9E293240,
    0xA1FDEAAF, 0x20A30A8C, 0x1CBD8053, 0x34F51B81, 0x2385CD90, 0x96AA3920,
    0xF5E2BE30, 0x49336625, 0x8D8C7CAC, 0xE218E266, 0x140AFB4B, 0xE3716DCE,
    0xC78D4357, 0xC7A08538, 0x012D82CC, 0xAE079F67, 0x1FC97F4D, 0x13B82CF6,
    0xA450A8F9, 0x3EF2B891, 0x37016870, 0x85837D47, 0x9E0554B9, 0x5E250425,
    0x924D3CF5, 0x1AA84C00, 0x27B42F8C, 0x49023610, 0xA7B73B7B, 0x62C8BCFD,
    0x3631472D, 0x0E33D2D6, 0x0A0B4B0A, 0x6A2556C3
    ], [
    0xD13723BF, 0x9414C5A7, 0x67FFF98A, 0x01945128, 0xD037928C, 0xDEC7C303,
    0x5EBD36AC, 0x5C905BCB, 0x020B6426, 0xB7C9C54D, 0x8613F926, 0x1FEC9118,
    0x51A1AA61, 0x16BA1018, 0x68338FC2, 0x5542A1ED, 0x8063E942, 0x8BAE40A2,
    0x1C5D6954, 0xA463AC5D, 0x3C301ED8, 0x4F4B860A, 0x6EE45E7C, 0xD462EE68,
    0xDFA82F0E, 0x763EB7CF, 0x78335FBC, 0x95EED064, 0xFB2F79D6, 0xECAA236A,
    0x59459EAE, 0x233D236A, 0x4DB2900A, 0x8B9D5EB4, 0x0F21ABB6, 0xFA27F2B1,
    0xA4A6FA51, 0x4653FD21, 0x93E9C526, 0xCCCB341B, 0x1F66711A, 0x68E054BE,
    0x7204FD43, 0x689E8AC0, 0x12302D1B, 0x96D11239, 0xB3DA833A, 0xCA15D14E,
    0x80D4798B, 0xB6465C4D, 0xDAAD50A1, 0x9FD6B95E, 0x1B4EFECB, 0xEA61AA1C,
    0x18AD77FA, 0x5A736118, 0x511A3385, 0xD5D92A85, 0xB957043E, 0xAA3554A6,
    0xFB571DF0, 0x305A86FE, 0x240E4572, 0x80DEFE96, 0x2888F5CC, 0x5272DAE5,
    0x1A283749, 0x0DF5E014, 0x1F6F2F7D, 0x292C1DC7, 0xC72ED514, 0x59E99AE5,
    0x0C4FDE67, 0xE30B2643, 0x24C12F6A, 0x4C9B0BFC, 0xA78F1A03, 0x0036C1DF,
    0x913B7309, 0x7FDE5A89, 0xFFC9D504, 0x9F7C42DB, 0x8BE84B2A, 0x588BBCFA,
    0x872C26E7, 0xA3BC8F41, 0x5B1160B4, 0x239B30E4, 0xB2DD5494, 0xE337530F,
    0xF113CD33, 0xEADC8DDF, 0xFF339D9F, 0x71F15A7D, 0x5973E16B, 0x5A4D3F0A,
    0x28656238, 0x0EB676E9, 0x5533A9A2, 0x07EAF535, 0x5C271A91, 0xDAFA35CF,
    0x8C0C4E34, 0xB2D15020, 0xA9CEAF93, 0x271157B4, 0x41B88963, 0x88EA0624,
    0x94400C7C, 0x650DCD70, 0xBAF8C4DF, 0x13DC1789, 0x0040522B, 0x13E1D0C1,
    0x80C2C55C, 0x4C22B92B, 0xC9BDFD2C, 0xAB74AB27, 0xAEAF6AD8, 0xA2A5E893,
    0x10A9000D, 0xDFE44794, 0xF94F9FA0, 0xFC7316A5, 0x7B967375, 0x0114700D,
    0xABFC7363, 0x09CB8915, 0xB96C9B3A, 0xE95142BD, 0x1B632A21, 0x57A66288,
    0x80B22AAD, 0xC276DBCC, 0x8C38D035, 0xB5AEC8CE, 0x4150EB32, 0xE8FE1512,
    0x184FE503, 0xC66A57A9, 0x25992BF4, 0x746F8100, 0xC7790E91, 0xE8988227,
    0x626C1812, 0xAA60037C, 0x43997BEA, 0x9508A877, 0x5AE80E46, 0xDE2758FF,
    0xF7E89EEE, 0x0EE387D6, 0xD763F872, 0x97D7F238, 0x910882D5, 0x6754994B,
    0x436C7433, 0x93210B5A, 0xCC33EBF7, 0xC530B930, 0x5DA8B772, 0x325DEC23,
    0x4599CC7B, 0x458591CC, 0x5A49130A, 0xB87F80DC, 0x708208CE, 0xE4B6033F,
    0xAFE91366, 0xCBAB3EF2, 0x718B84B8, 0x60859C5E, 0x50701AEC, 0x05E2CA48,
    0xB2BFCDB8, 0xFD47C881, 0xB18ECC02, 0xF8AABC72, 0x0ABD654B, 0x1A6602C0,
    0xFFFBCED7, 0xDA627448, 0x67E0590A, 0x3B1952D9, 0x4C0A32FB, 0xE9F0507C,
    0x830699D6, 0xCF481E29, 0x3FEC6807, 0x18CB4E71, 0x6CAC5839, 0x592E5FD5,
    0x1A2B1DA2, 0xA104840F, 0xE2B4A352, 0xAA202DF2, 0xC1E3D837, 0x4DA02F3A,
    0xB8AE3EE2, 0xBAF2AD8F, 0x60A0A49C, 0x03A08BA2, 0x3C7613FC, 0xC42B5AA7,
    0xFB799A04, 0x3FC12E4F, 0xE2F8881A, 0x854B6F93, 0x732EB662, 0xF04CB10D,
    0x3BBDFD40, 0x9B1F08BD, 0x679E054C, 0x5A5C81B9, 0x42EEF45A, 0xE1CAC282,
    0x8E057351, 0x618F3140, 0x2B4EB7BE, 0x7C0B4416, 0xD67CF521, 0x61B12968,
    0x12454732, 0x09E117B5, 0x427F05E2, 0x151256A0, 0xD4DE1087, 0x610F2E55,
    0x0703EDE6, 0xD984D328, 0x3F349754, 0x76E7FB05, 0x8C339292, 0x1C7B2C00,
    0xEAD34339, 0xEED62B3B, 0x8AD430DE, 0x56977BEE, 0xC73DB96B, 0x74ECF426,
    0xEC27F20A, 0xD250B1B3, 0xD1DB7436, 0xBADF98FB, 0xB5DBF4B7, 0xC87589C2,
    0xD634D942, 0xB5A2FEF4, 0x7FD6F13B, 0xB89DA34D, 0x9FC14AFC, 0x733563D4,
    0xCEE0EE6A, 0x5699CCBE, 0xD684349C, 0xFD2CCCEC
    ], [
    0x9CE989E5, 0x27A0CB56, 0x02E00928, 0xABBA6B68, 0xD721FCCF, 0xA696A7B7,
    0x36CE1D63, 0x9B4287D7, 0x390743D1, 0x69F6BB36, 0x93D521C5, 0x44D162AD,
    0xE0070AA9, 0x5FB59DC6, 0x19542E39, 0x26F788BE, 0x75FE89D6, 0x0C80CFCB,
    0x0540DC8C, 0xFA54F755, 0xF773FC82, 0xA35D570A, 0xC5723322, 0x25EF7BDE,
    0x87F8E80A, 0x946960D7, 0x1ADBD76B, 0x750C1AF9, 0x0360C46B, 0x8446D2A9,
    0xC9202B06, 0x278C843A, 0x5DA07CCF, 0x2245D4AA, 0x93DFF522, 0x192AAAEF,
    0x9CDE5DD9, 0x3D9794C2, 0xF3E16F90, 0x36CCC653, 0xF16949C0, 0xD8554E88,
    0x5824EC8E, 0xE311AAE5, 0xFDBC7A0D, 0x10F4AD37, 0xF468F494, 0x4E4F819D,
    0x3F9E57EA, 0xE43EC4AD, 0x871F2076, 0x4ADDB4F1, 0xE8E299B3, 0x7C0A1836,
    0x941F36A1, 0x35457B0F, 0x39470AEC, 0x9A5B504B, 0x4BB2F6F8, 0xF6DE598B,
    0x8CFEC07F, 0x4548D4BF, 0xD41E0229, 0x3F2A12F7, 0x1AF19BD5, 0x644175B5,
    0xA4CA85FC, 0x49276FD6, 0xF62A4D52, 0x210A6846, 0xBB56CCC3, 0x031158B2,
    0x4DC62335, 0x10FFA10E, 0xF055BCEF, 0xC5AA7928, 0x1434F73C, 0x2A43ECA8,
    0x842132AC, 0xD562AD21, 0xD5CEC47C, 0x1B691AB0, 0x42767035, 0x13BA59C9,
    0x29AF3D75, 0xB39E2850, 0x31D492A5, 0x7D9E2352, 0x6AF923E1, 0x3BE1D419,
    0x16158A7E, 0x44EF7376, 0x2EE3C6C1, 0x8D352616, 0x9CB629BA, 0x6208B9AD,
    0x0B631F69, 0x8F571F32, 0xB0D68B0C, 0xCDFAD3A0, 0xC80875FE, 0x59E9602F,
    0x51F6B69A, 0x1D409455, 0x61FDB55C, 0xCE3720E3, 0x137DE790, 0x8B04FC1B,
    0xC19CE38A, 0xB29D90F9, 0xD3593524, 0x1627951C, 0x5C11A5DE, 0xAF94409B,
    0xC832B671, 0x32B72AE3, 0xBA6BB680, 0xA12ACF8A, 0xE4A9D43F, 0x107B81B9,
    0x53B1B9D5, 0x8DA00BB9, 0x2C148921, 0x151EC1A6, 0xD768ECCD, 0x436855B7,
    0x8E33A334, 0xEBB502A1, 0x2ECCD157, 0x39F958F6, 0x9A325E5B, 0xDAEE53FE,
    0x0278EC16, 0xADDE5BFB, 0x9248885D, 0x413218E1, 0x1C63F37E, 0x4D0C747C,
    0x9135BAF4, 0xD86EEEED, 0x73D431C5, 0x28328C16, 0x6BCF2229, 0x46DCDB0D,
    0xDF1A50DC, 0x9860C3AC, 0x708CD67D, 0xF6872403, 0x522D6E98, 0xE6CEA50F,
    0xAAC9DC07, 0xD5605E8D, 0xE56E2CA7, 0x47FD227D, 0xF8210DDD, 0x0F3F974C,
    0x062E6E11, 0x4EFF4F43, 0xA61945E3, 0xED59FF50, 0x6094DBED, 0x70383AE0,
    0xE2B55F47, 0x81212B70, 0xD47B4D3D, 0x727BFD92, 0x607A07EC, 0xEE4AA97B,
    0xFE9FCC26, 0xBB2FEEA4, 0x31C4E4CC, 0xA06ACF19, 0xD7A8A983, 0xC7A038EA,
    0xBC89CB3C, 0x58C63BF6, 0xC60A7E0D, 0x1DC2A9DF, 0x5F7C8278, 0x616B32FA,
    0x3475A5E2, 0x608A8F4C, 0x7EC19DD7, 0x0CD2C716, 0xAE46828E, 0xE53B17FA,
    0xF5A4AD4D, 0x0B1290AE, 0x5C6E74D3, 0x866D7316, 0x39054DF1, 0xA2A818BB,
    0x42D6C33A, 0xB6FEC0F2, 0xA1D3B572, 0x6F48AD3E, 0x6144A64A, 0x7BF86B73,
    0x0E444BC4, 0x9AD01D4A, 0x43C3C4EB, 0x9D518FCD, 0x1CE1B720, 0xAD579F70,
    0xB2EECB4F, 0x9502AFC7, 0xEBC647A0, 0xB7FA1B5C, 0x3ACC4F6E, 0x047E7148,
    0x496E7AC9, 0x7F136464, 0x41C36E76, 0xCC38CB5E, 0xB24F9444, 0x2E95E3B1,
    0xDE7DE530, 0xCDCA74C3, 0x044AA504, 0xFA4B351F, 0xFBC33DA2, 0x14FB5DAC,
    0x179B39C8, 0xBD4A85E1, 0x3EFAAD11, 0x2C2C6F71, 0xE92A32C4, 0x76B6C150,
    0xE1FE212C, 0xF38FD4CF, 0x268C35D9, 0xEDB03308, 0x7B8E2CB5, 0xB3706839,
    0x8144E420, 0xF6CECF27, 0x0DE5225D, 0x5D40CD54, 0x8B42965C, 0x7295E976,
    0x844D6827, 0x881E23DF, 0x964A45F1, 0x528E84E8, 0x57DA399F, 0xD7903C7A,
    0x78B0FEE1, 0xB8D3A5D7, 0x2C9A9DE1, 0x4D73B1FD, 0xE3558381, 0x8B0434E1,
    0xBF918EBC, 0x7248BC30, 0xA19E9B98, 0x128E7B36
]]










player = """<!DOCTYPE html>
<html>
    <body>
        <audio preload="none" autoplay="autoplay" controls="controls">
            <source src="http://localhost:8080/m" type="audio/mp3"/>
            Your browser does not support the audio element.
        </audio>
    </body>
</html>
"""














class WebConnection(object):
    def __init__(self, sock):
        self.sock = sock
        
        self._path = None
        self._path_gen = None
        
        self._stream_gen = None
        self._last_streamed = ""
        
    def fileno(self):
        return self.sock.fileno()
    
    @property
    def path(self):
        if self._path: return self._path
        if not self._path_gen: self._path_gen = self.read_request()
        self._path = self._path_gen.next()
        return self._path
    
    def close(self):
        self.sock.shutdown(socket.SHUT_RDWR)
        self.sock.close()
    
    def read_request(self):            
        agg_data = ""
        while True:
            try: data = self.sock.recv(1)
            except socket.error, e:
                if e.errno == errno.EWOULDBLOCK:
                    yield False
                    continue
                else:
                    print sys.exc_info()
                    yield True
                    raise StopIteration
                
            agg_data += data
            if "\r\n" in agg_data: break
        path = agg_data.strip().split()[1]
        yield path      


    def serve_webpage(self):
        page = player
        try:
            self.sock.send("HTTP/1.1 200 OK\r\nContent-Length: %s\r\n\r\n" % len(page))
            self.sock.send(page)
        except:
            print sys.exc_info()
    

    def stream_music(self, music):
        if not self._stream_gen:
            self._stream_gen = self.send_stream(music)
            done = self._stream_gen.next()
        else: done = self._stream_gen.send(music)
        return done            

    def send_stream(self, music):
        self.sock.send("HTTP/1.1 200 OK\r\n\r\n")
        
        while True:
            if self._last_streamed != music:
                try: sent = self.sock.send(music)
                except socket.error, e:
                    if e.errno == errno.EWOULDBLOCK:
                        pass
                    else:
                        print sys.exc_info()
                        break
                
            self._last_streamed = music
            music = (yield False)   
        yield True
        



        
class PlayerServer(object):
    def __init__(self):
        self.to_read = set()
        self.to_write = set()
        self.to_err = set()
        self.callbacks = []
        self.music_buffer = ""

    def serve(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('', 8080))
        server.listen(100)
        server.setblocking(0)
        
        self.to_read.add(server)
        last_music_read = time.time()
        
        while True:
            read, write, err = select.select(
                self.to_read,
                self.to_write,
                self.to_err,
            )
            
            for sock in read:
                if sock is server:
                    conn, addr = server.accept()
                    conn.setblocking(0)
                    
                    conn = WebConnection(conn)
                    self.to_read.add(conn)
                    self.to_err.add(conn)
                    
                elif isinstance(sock, Song):
                    chunk = sock.read()
                    if chunk: self.music_buffer = chunk
                else:
                    if sock.path:                    
                        #sock.shutdown(socket.SHUT_RD)
                        self.to_read.remove(sock)
                        self.to_write.add(sock)
                    
            for sock in write:
                if sock.path == "/":
                    sock.serve_webpage()
                    sock.close()
                    self.to_write.remove(sock)
                    self.to_err.remove(sock)
                    
                elif sock.path == "/m":
                    done = sock.stream_music(self.music_buffer)
                    if done: self.to_write.remove(sock)
                    
                else:
                    sock.close()
                    self.to_write.remove(sock)
                    self.to_err.remove(sock)
                    
            for cb in self.callbacks: cb()
            time.sleep(.01)
            
            
            


















logging.basicConfig(
    format="(%(process)d) %(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

if __name__ == "__main__":
    parser = OptionParser(usage=("%prog [options]"))
    parser.add_option('-u', "--username", dest="user", help="your Pandora username (your email)")
    parser.add_option('-p', '--password', dest='password', help='your Pandora password')
    parser.add_option('-d', '--debug', dest='debug', action="store_true", default=False, help='debug XML to/from Pandora')
    (options, args) = parser.parse_args()

    if not options.password or not options.user:
        parser.error("Please provide your username and password with -u and -p")

    if options.debug:
        debug_logger = logging.getLogger("debug_logger")
        debug_logger.setLevel(logging.DEBUG)
        lh = logging.FileHandler(join(gettempdir(), "pypandora_debugging.log"))
        lh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        debug_logger.addHandler(lh)

    account = Account(options.user, options.password, debug=options.debug)

    # play a random station
    from random import choice
    random_station = choice(account.stations.values())
    song = random_station.playlist[0]


    server = PlayerServer()
    server.to_read.add(song)
    server.serve()
