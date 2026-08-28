import os
def main():
	output = os.path.join(os.path.expanduser('~'), 'Desktop', 'peak_drift2.txt')
	info('AF Demag testing')
	pp2= 0
	for i in xrange(4):
		info('Doing iteration ={}'.format(i))
		position_magnet('Ar36','L2(CDD)')
		p1 = peak_center('CDD_on_36')
		if p1 is None:
			return

		position_magnet('Ar38','AX(CDD)')
		sleep(5)
		
		p2 = peak_center('AX(CDD)_Ar38')
		if p2 is None:
			return
		
		position_magnet('PHHCbs', 'AX(CDD)')
		sleep(5)
		
		p3 = peak_center('CDD_on_36')
		if p3 is None:
			return

		dev=p1-p3
		pdev = pp2-p2
		info('p1={} p3={} dev={}, p2={}, pp2={}, dev={}'.format(p1, p3, dev, p2, pp2, pdev))
		with open(output, 'a') as wfile:
			wfile.write('{},{},{}, {},{},{}\n'.format(p1,p3,dev, p2, pp2, pdev))
		pp2 = 
		sleep(5)
		
		
