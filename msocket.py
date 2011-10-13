import socket
import errno



class MagicSocket(object):
    def __init__(self, sock):
        self.sock = sock
        self._read = []
        
    def read_until(self, *delims, **kwargs):
        buf = kwargs.get("buf", 1024)
        read = ""
        cursor = 0
        last_cursor = None
        first_find = None
        
        delims = list(delims)
        delims.reverse()
        current_delim = delims.pop()
        
        while True:
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
                        yield read[first_find:found]  
             
            last_cursor = None
            
            try: data = self.sock.recv(buf)
            except socket.error, err:
                if err.errno is errno.EWOULDBLOCK:
                    yield None
            else:
                if not data: yield False
                read += data
                
                
host = "python.org"
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.connect((host, 80))
sock.send("GET / HTTP/1.1\r\nHost: %s\r\n\r\n" % host)

msock = MagicSocket(sock)
gen = msock.read_until("\r\n\r\n")
print gen.next()     