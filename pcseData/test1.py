import pcse
import os
from pcse.input import CABOFileReader, WOFOST72SiteDataProvider, YAMLAgroManagementReader, OpenMeteoWeatherDataProvider
from pcse.base import ParameterProvider
wdp = OpenMeteoWeatherDataProvider(latitude=38.60819, longitude=27.08609) 
print(wdp)
cropfile = os.path.join('sug0601.crop')
soilfile = os.path.join('ec3.soil')


custom_WAV = {"WAV": 100, "SMLIM": 0.36, "SSI": 0}
agromanagement_file = os.path.join('sugarbeet_calendar.agro')
agromanagement = YAMLAgroManagementReader(agromanagement_file)
sitedata = WOFOST72SiteDataProvider(**custom_WAV)


soildata = CABOFileReader(soilfile)
cropdata = CABOFileReader(cropfile)

parameters = ParameterProvider(cropdata=cropdata, soildata=soildata, sitedata=sitedata)