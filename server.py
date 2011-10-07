import select
import socket
import time
import errno



server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(('', 8081))
server.listen(100)
server.setblocking(0)


connections = {}
to_read = set([server])
to_write = set([])
to_err = set([])

res_template = "HTTP/1.1 200 OK\r\n\r\n"




def read_socket(sock, to_read, buf=1024):
    agg_data = ''
    while True:
        try: data = sock.recv(buf)
        except socket.error, e:
            if e.errno == errno.EWOULDBLOCK:
                to_read.add(conn)
                yield False, agg_data
                continue
        if not data: break
        agg_data += data
    yield True, agg_data
        


while True:
    read, write, ex = select.select(to_read, to_write, to_err)
    
    for sock in read:
        if sock is server:
            conn, addr = server.accept()
            conn.setblocking(0)
            to_read.add(conn)
        else:
            gen = read_socket(conn, to_read)
            print gen.next()
            
    for sock in write:
        pass
    
    continue
    
    select.select
    print 'Connected by', addr
    while 1:
        data = conn.recv(1024)
        if not data: break
        conn.send(data)