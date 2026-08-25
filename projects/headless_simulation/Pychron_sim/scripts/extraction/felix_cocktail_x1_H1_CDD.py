'''
modifier: 03
eqtime: 25
'''

def main():
    info("Cocktail Pipette x1")
    gosub('felix:WaitForMiniboneAccess')
    gosub('felix:PrepareForAirShot')
    open('Q')
    open('D')
    gosub('common:EvacPipette1')
    gosub('common:FillPipette1')
    gosub('felix:PrepareForAirShotExpansion')
    gosub('common:ExpandPipette1')
    close('E')
    close('D')
    sleep(duration=2.0)
    close(description='Outer Pipette 1')
    