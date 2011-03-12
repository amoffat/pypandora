from SimpleXMLRPCServer import SimpleXMLRPCServer
from SimpleXMLRPCServer import SimpleXMLRPCRequestHandler
import pandora



    
def start_server(ip="localhost", port=8123):
    server = SimpleXMLRPCServer((ip, port))
    server.register_introspection_functions()

    #server.register_instance(MyFuncs())
    #server.register_function(adder_function, 'add')
    
    server.serve_forever()