'''
'''

def main():
    info('syn extraction measurement script')
    
    activate_detectors('H2','H1','AX(CDD)','L1','L2(CDD)')
    position_magnet('Ar40', 'H2')
    set_baseline_fits()

    set_time_zero()
    baselines(ncounts=35, mass=39.862, detector='H2', check_conditionals=False)
    
    info('finished measure script')