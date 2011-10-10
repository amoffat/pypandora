import select
import socket
import time
import errno
import sys
import time


server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('', 8081))
server.listen(100)
server.setblocking(0)

music_source = open("/home/amoffat/Music/windowlicker.mp3", "r")
music_buffer = music_source.read(4096)
last_music_read = time.time()

to_read = set([server, music_source])
to_write = set([])
to_err = set([])



class Connection(object):
    def __init__(self, sock):
        self.sock = sock
        
        self._path = None
        self._path_gen = None
        
        self._stream_gen = None
        self._last_streamed = None
        
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
                    to_read.add(conn)
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
            
        if done: to_write.remove(self)
            

    def send_stream(self, music):
        self.sock.send("HTTP/1.1 200 OK\r\n\r\n")
        
        while music:
            if music != self._last_streamed:
                try:
                    self.sock.sendall(music)
                    self._last_streamed = music
                except socket.error, e:
                    if e.errno == errno.EWOULDBLOCK:
                        pass
                    else:
                        print sys.exc_info()
                        break
                
            music = (yield False)   
        yield True
        
    


while True:
    read, write, ex = select.select(to_read, to_write, to_err)
    
    for sock in read:
        if sock is server:
            conn, addr = server.accept()
            conn.setblocking(0)
            
            conn = Connection(conn)
            to_read.add(conn)
        elif sock is music_source:
            now = time.time()
            if last_music_read + .1 < now:
                music_buffer = music_source.read(4096)
                last_music_read = now
        else:
            if sock.path:                    
                #sock.shutdown(socket.SHUT_RD)
                to_read.remove(sock)
                to_write.add(sock)
            
    for sock in write:     
        if sock.path == "/":
            sock.stream_music(music_buffer)
