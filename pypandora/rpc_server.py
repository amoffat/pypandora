import eventlet
eventlet.monkey_patch()
from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import pypandora
import logging



def format_song(song):
    """ helper function for formatting songs over rpc """
    return {
        "id": song.id,
        "title": song.title,
        "artist": song.artist,
        "album": song.album,
        "length": song.length,
        "progress": song.progress
    }    

class PandoraServerProxy(object):
    def __init__(self):
        self.account = None
        
    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if name == "account" and not attr: raise Exception, "please login first"
        return attr
    
    def login(self, username, password):
        self.account = pypandora.Account(username, password)
        
    def next_song(self):
        pass
    
    def previous_song(self):
        pass
    
    def get_station(self, station_id):
        station = self.account.stations.get(station_id, None)
        if not station: raise KeyError, "no station by key %s" % station_id
        return station
    
    def stop_song(self):
        self.account.stop()
        
    def get_playlist(self, station_id):
        station = self.get_station(station_id)
        playlist = []
        for song in station.playlist:
            playlist.append(format_song(song))
        return playlist
        
    def play_station(self, station_id):
        station = self.get_station(station_id)
        song = station.play(block=False, finished_cb=station.finish_cb__play_next)
        if not song: return False
        return format_song(song)
    
    def get_stations(self):
        if not self.account: return {}
        return dict([(k, s.name) for k,s in self.account.stations.iteritems()])
    
    def current_station(self):
        station = self.account.current_station
        if not station: raise Exception, "no current station"
        return (station.id, station.name) 
    
    def current_song(self):
        song = self.account.current_song
        if not song: raise Exception, "no current song playing"
        return format_song(song)


    
        
def serve(ip="localhost", port=8123):
    server = SimpleXMLRPCServer((ip, port), allow_none=True)
    server.register_introspection_functions()

    server.register_instance(PandoraServerProxy())    
    t = eventlet.spawn(server.serve_forever)
    t.wait()
    
    
    
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    serve()