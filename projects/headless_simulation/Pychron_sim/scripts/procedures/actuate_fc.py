'''
'''
N=20
VALVE='FC'
def main():
    info("Actuate {} a bunch of times".format(VALVE))
    
    for i in xrange(N):
        info('Iteration= {:03n}/{:03n}'.format(i,N))
        if i%2:
            close(VALVE)
        else:
            open(VALVE, cancel_on_failed_actuation=False, ntries=5)
        sleep(5)
            
    close(VALVE)
