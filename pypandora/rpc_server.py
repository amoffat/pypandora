from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import pypandora




class PandoraServerProxy(object):
    def __init__(self):
        self.account = None
    
    def login(self, username, password):
        self.account = pypandora.Account(username, password)
        
    def next_song(self):
        pass
    
    def previous_song(self):
        pass
    
    def get_playlist(self, station_id):
        if not self.account: return {}
        station = self.account.stations.get(station_id, None)
        if not station: raise KeyError, "no station by key %s" % station_id

        playlist = {}
        for song in station.playlist:
            playlist[song.id] = {
                "title": song.title,
                "artist": song.artist,
                "album": song.album
            }
        return playlist
        
    
    def get_stations(self):
        if not self.account: return {}
        return dict([(k, s.name) for k,s in self.account.stations.iteritems()])
    
    def song_info(self):
        pass


    
        
def serve(ip="localhost", port=8123):
    server = SimpleXMLRPCServer((ip, port), allow_none=True)
    server.register_introspection_functions()

    server.register_instance(PandoraServerProxy())    
    server.serve_forever()
    
    
    
if __name__ == "__main__":
    serve()