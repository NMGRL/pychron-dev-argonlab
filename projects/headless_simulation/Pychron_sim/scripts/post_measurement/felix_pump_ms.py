def main():
	info('Pumping spectrometer')
	open(name='V', cancel_on_failed_actuation=False)
	