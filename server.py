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


connections = {}
listeners = {}
to_read = set([server])
to_write = set([])
to_err = set([])


h = open("/home/amoffat/Music/windowlicker.mp3", "r")



def read_socket(sock, to_read, buf=1):    
    agg_data = ""
    while True:
        try: data = sock.recv(buf)
        except socket.error, e:
            if e.errno == errno.EWOULDBLOCK:
                to_read.add(conn)
                yield False
                continue
        agg_data += data
        if "\r\n" in agg_data: break
    path = agg_data.strip().split()[1]
    yield path
        


def stream_music(sock):
    i = 0
    music = h.read(4096)
    sock.send("HTTP/1.1 200 OK\r\n\r\n")
    
    done = False
    while music:
        try: print i, len(music), sock.send(music)
        except socket.error, e:
            if e.errno == errno.EWOULDBLOCK:
                yield False
            else:
                done = True
                print sys.exc_info()
                break                
            
        music = h.read(4096)
        i += 1
        #time.sleep(.01)
        
    if done:
        print "CLOSING"
        #sock.shutdown(socket.SHUT_)
        to_write.remove(sock)
      
    yield True
    


while True:
    read, write, ex = select.select(to_read, to_write, to_err)
    
    for sock in read:
        if sock is server:
            conn, addr = server.accept()
            conn.setblocking(0)
            
            gen = read_socket(conn, to_read)
            connections[conn] = gen
            to_read.add(conn)
        else:
            gen = connections[sock]
            path = gen.next()
            if path:
                print path
                
                if path == "/":
                    listeners[sock] = stream_music(sock)
                    
                sock.shutdown(socket.SHUT_RD)
                to_read.remove(sock)
                to_write.add(sock)
            
    for sock in write: 
        gen = listeners.get(sock, None)       
        if gen:
            done = gen.next()
            if done:
                del listeners[sock]
                #to_write.remove(sock)
