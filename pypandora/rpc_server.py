from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import pypandora




class PandoraServerProxy(object):
    
    def login(self, username, password):
        self.account = pypandora.Account(username, password)
        
    def next_song(self):
        pass
    
    def previous_song(self):
        pass
    
    def get_stations(self):
        pass
    
    def song_info(self):
        pass


    
def start_server(ip="localhost", port=8123):
    server = SimpleXMLRPCServer((ip, port))
    server.register_introspection_functions()

    server.register_instance(PandoraServerProxy())    
    server.serve_forever()