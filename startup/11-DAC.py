from ophyd import EpicsMotor, Device, Component as Cpt

class DiamondAnvil(Device):
    x = Cpt(EpicsMotor, 'C-ES{DET:1-Ax:Z}Mtr')
    y = Cpt(EpicsMotor, 'C-ES{DET:1-Ax:Y}Mtr')
    z = Cpt(EpicsMotor, '-ES:1:m7')

#DACx = EpicsMotor('XF:04BMC-ES{DET:1-Ax:Z}Mtr', name='DAC X')
#DACy = EpicsMotor('XF:04BMC-ES{DET:1-Ax:Y}Mtr', name='DAC Y')
#DACz = EpicsMotor('XF:04BM-ES:1:m7',            name='DAC Z')

DAC = DiamondAnvil('XF:04BM', name='DAC')
