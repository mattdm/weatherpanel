import displayio

from line import line_generator

from adafruit_bitmap_font import bitmap_font
from adafruit_display_text.label import Label

import matrix


QUERY_COLOR = 0x4278ff
SUCCESS_COLOR = 0x42ff78
FAILURE_COLOR = 0xff6a00

class Display:


    def __init__(self,config):
        
        
        font_dogica_pixel8 = bitmap_font.load_font("/fonts/dogica-pixel-8.pcf")

        temperature_colors = [
                              0xFFFFFF,
                              0x174afd,
                              0x4278ff,
                              0x6f9dff,
                              0x9ebfff,
                              0xcedfff,
                              0xdddddd,
                              0xffe2cf,
                              0xffc6a0,
                              0xffa872,
                              0xff8a43,
                              0xff6a00,
                             ]        
        self.temperature_palette = displayio.Palette(len(temperature_colors))
        self.temperature_palette.make_transparent(0)
        for i in range(0,len(temperature_colors)):
            self.temperature_palette[i] = temperature_colors[i]

        precipitation_colors = [ 
                                0xFF0000,
                                0x0000D0, # rain
                                0xFFFFFF, # snow
                               ]
        self.precipitation_palette = displayio.Palette(len(precipitation_colors))
        self.precipitation_palette.make_transparent(0)
        for i in range(0,len(precipitation_colors)):
            self.precipitation_palette[i] = precipitation_colors[i]


        self.root_group = displayio.Group()
        matrix.display_set_root(self.root_group,swapgb=config['SWAP_GREEN_BLUE'])
        
        # status        
        self.status_group = displayio.Group(x=0,y=0)
        self.network_label = Label(font_dogica_pixel8, text="", color=QUERY_COLOR, x=0, y=12)
        self.location_label = Label(font_dogica_pixel8, text="", color = QUERY_COLOR, x=0, y=20)
        self.station_label = Label(font_dogica_pixel8, text="", color=QUERY_COLOR, x=0, y=28)
        self.status_group.append(self.network_label)
        self.status_group.append(self.location_label)
        self.status_group.append(self.station_label)
        self.root_group.append(self.status_group)

        # hourly temp and precipitation lines display
        self.hourly_group = displayio.Group(x=0,y=0)
        self.precipitation_forecast_bitmap = displayio.Bitmap(64,32,len(self.precipitation_palette))
        self.precipitation_forecast_grid = displayio.TileGrid(bitmap=self.precipitation_forecast_bitmap, pixel_shader=self.precipitation_palette, tile_width = self.precipitation_forecast_bitmap.width, tile_height = self.precipitation_forecast_bitmap.height)
        self.temperature_forecast_bitmap = displayio.Bitmap(64,32,len(self.temperature_palette))
        self.temperature_forecast_grid = displayio.TileGrid(bitmap=self.temperature_forecast_bitmap, pixel_shader=self.temperature_palette, tile_width = self.temperature_forecast_bitmap.width, tile_height = self.temperature_forecast_bitmap.height)
        self.hourly_group.append(self.precipitation_forecast_grid)
        self.hourly_group.append(self.temperature_forecast_grid)
        self.root_group.append(self.hourly_group)

        # clock and temp
        self.timetemp_group = displayio.Group(x=0,y=0)
        self.clock_label = Label(font_dogica_pixel8, text="", color=0xFFFFFF, anchor_point = (1,0), anchored_position=(65, 0))
        self.current_temp_label = Label(font_dogica_pixel8, text="", color=0x808080, x=-1, y=4)
        self.timetemp_group.append(self.clock_label)
        self.timetemp_group.append(self.current_temp_label)
        self.root_group.append(self.timetemp_group)

    def set_status(self,label,status,text):
        
        if label == "network":
            l = self.network_label
        elif label == "location":
            l = self.location_label
        elif label == "station":
            l = self.station_label
        else:
            raise(ValueError)
        
        if status == "query":
            l.color = QUERY_COLOR
        elif status == "failure":
            l.color = FAILURE_COLOR
        elif status == "success":
            l.color = SUCCESS_COLOR
        else:
            raise(ValueError)
        
        self.status_group.hidden=False

        # print(f"[ {label}: {status} / {text} ]")
        l.text = text

    def clear_status(self):
        self.status_group.hidden=True
        self.network_label.text=""
        self.location_label.text=""
        self.station_label.text=""
        self.network_label.color=QUERY_COLOR
        self.location_label.color=QUERY_COLOR
        self.station_label.color=QUERY_COLOR


    def update_time(self,clock):
        self.clock_label.text = clock.pretty_time
        self.clock_label.color = clock.color       

        self.timetemp_group.hidden = False

        
    def update_hourly_forecast(self,hourly_data,historical_data,current_time):
    
        height = self.temperature_forecast_bitmap.height
        width = self.temperature_forecast_bitmap.width

        # TODO: make this configurable
        scale_range = 110
        scale_factor = scale_range / height
        midpoint_temp = 50


        x = 0
        peakpoint = height
        valleypoint = 0

        previous_point = None

        print("Plotting hours",end="")
        for hour in hourly_data:

            if hour.end < current_time:
                print(f"\nHour {x:2} expired at {hour.end}")
                continue            

            hourly_temp_point = max(0,min(height-1,round(height//2+(midpoint_temp-hour.temperature)/scale_factor)))


            # this is to decide if we need to move the time / temp labels.
            # only consider peaks and valleys that are in the area of the text
            if x < self.current_temp_label.width or x > width - max(17,self.clock_label.width):
                if hourly_temp_point < peakpoint:
                    peakpoint = hourly_temp_point
                if hourly_temp_point > valleypoint:
                    valleypoint = hourly_temp_point

            if peakpoint < 8:
                if valleypoint < 24:
                    self.timetemp_group.y = valleypoint + 3
                else:
                    self.timetemp_group.y = 14 # too many extremes. give up, center it.
            else:
                self.timetemp_group.y = 0


            # clear the column
            for y in range(0,height):
                self.temperature_forecast_bitmap[x,y] = 0

            color = self._temp_color_index(hour.temperature,historical_data)            
            # then draw the hourly temperature forecast
            if x>0 and previous_point and abs(previous_point - hourly_temp_point) > 1:
                # draw line back to previous point so there's no ugly gaps
                for (line_x,line_y) in line_generator((x,hourly_temp_point),(x-1,previous_point)):
                    self.temperature_forecast_bitmap[line_x,line_y] = color
            else:
                # just draw the dot
                self.temperature_forecast_bitmap[x,hourly_temp_point] = color

            # we use the first hour's data for the current temperature
            if x == 0:
                self.current_temp_label.text = f"{hour.temperature}°"
                self.current_temp_label.color = self.temperature_palette[color]

            if hour.precipitation:
                hourly_precipitation_point = height-int(((hour.precipitation / 100) * height) + 0.5)
            else:
                # if there's no rain, erase it all
                hourly_precipitation_point = height

            for y in range(0,height):
                if y >= hourly_precipitation_point:
                    # TODO: check the forecast codes for rain or snow
                    self.precipitation_forecast_bitmap[x,y] = 1
                else:
                    self.precipitation_forecast_bitmap[x,y] = 0


            x += 1
            previous_point = hourly_temp_point
            print(".",end="")
            if x >= self.temperature_forecast_bitmap.width:                
                break # end of panel

        # TODO If we run out of hours before we get to bitmap width, clear the
        # remaining columns. Could then use this count to determine if the whole
        # forecast is too out of date.
        print() # end dot-per-hour printout
        
    



        return True

    def _temp_color_index(self,temperature,historical=None):
        center = len(self.temperature_palette)//2
        buckets = center-1 # because we reserve 0 for clear

        if not historical:
            return center
        if temperature < historical['ave-low']:
            return center-min(buckets,int((temperature-historical['ave-low'])/((historical['low']-historical['ave-low'])/buckets)))
        if temperature > historical['ave-high']:
            return center+min(buckets,int((temperature-historical['ave-high'])/((historical['high']-historical['ave-high'])/buckets)))
        return 6

    def _temp_color(self,temperature,historical=None):
        return self.temperature_palette[self._temp_color_index(temperature,historical)]