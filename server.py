import select
import socket
import time
import errno
import sys
import time
from collections import deque







class Connection(object):
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
        self.to_read = set([music_source])
        self.to_write = set()
        self.to_err = set()
        self.callbacks = []
        self.music_buffer = ""

    def serve(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('', 8081))
        server.listen(100)
        server.setblocking(0)
        
        self.to_read.add(server)
        last_music_read = time.time()
        
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
                    
                    conn = Connection(conn)
                    self.to_read.add(conn)
                    
                elif sock is music_source:
                    now = time.time()
                    if last_music_read + .1 < now:
                        self.music_buffer = sock.read(4096)
                        last_music_read = now
                    else:
                        time.sleep(.1)
                else:
                    if sock.path:                    
                        #sock.shutdown(socket.SHUT_RD)
                        self.to_read.remove(sock)
                        self.to_write.add(sock)
                    
            for sock in write:     
                if sock.path == "/":
                    done = sock.stream_music(self.music_buffer)
                    if done: self.to_write.remove(sock)
                    
            for cb in self.callbacks: cb()



server = PlayerServer()
server.serve()