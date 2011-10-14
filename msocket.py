import socket
import errno



class MagicSocket(object):
    def __init__(self, sock):
        self.sock = sock
        self.tmp_buffer = ""
        self._read_gen = None
        
    def read_until(self, *delims, **kwargs):
        if not self._read_gen: self._read_gen = self._read_until(*delims, **kwargs)
        ret =  self._read_gen.next()
        if ret: self._read_gen = None
        return ret
        
    def _read_until(self, *delims, **kwargs):
        buf = kwargs.get("buf", 1024)
        
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
            return read + self.sock.recv(buf)
        
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
                            
                            self.tmp_buffer = read[found:]
                            yield read[first_find:found]
             
                last_cursor = None
            
            
            try: data = recv(buf)
            except socket.error, err:
                if err.errno is errno.EWOULDBLOCK:
                    yield None
            else:
                if not data: yield False
                read += data
                if num_bytes and len(read) >= num_bytes:
                    self.tmp_buffer = read[num_bytes:]
                    yield read[:num_bytes]
                
                
                
host = "python.org"
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((host, 80))
sock.send("GET / HTTP/1.1\r\nHost: %s\r\n\r\n" % host)

msock = MagicSocket(sock)
headers = msock.read_until("\r\n\r\n")
headers = headers.split("\r\n")

headers = dict([h.split(": ") for h in headers[1:]])

length = int(headers["Content-Length"])
print msock.read_until(length)