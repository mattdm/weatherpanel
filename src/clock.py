import time

import rtc


import network
import dstrule

COLOR_NORMAL = 0xFFFFFF
COLOR_ERROR = 0xFF0080
COLOR_UNCERTAIN = 0x8000FF

TIME_UNKNOWN = "" # "--:--"

class Clock():


    def __init__(self,config):
        
        
        if 'CLOCK_TWENTYFOUR' in config.keys():
            self.twentyfour=bool(config['CLOCK_TWENTYFOUR'])
        else:
            self.twentyfour=False
        if 'CLOCK_DELIMINATOR' in config.keys():
            self.delim=config['CLOCK_DELIMINATOR'][0]
        else:
            self.delim=':'

        self.color = COLOR_ERROR
        self.tz=None
        self.__dstrule=None

        self.ntp = network.ntp()
        self.rtc = rtc.RTC()

    def sync_network_time(self):
        

        timedelta=0
        tries = 0
        while tries < 5:
            try:         
                print("Getting network time.")
                network_time = self.ntp.datetime
                timedelta = time.mktime(network_time) - time.mktime(self.rtc.datetime)
                self.rtc.datetime = network_time
                print(f"Time is now {self.isotime} (adjusted by {timedelta:+})")
                self.color=COLOR_NORMAL
                break
            except OSError as e:
                print(f"{e}")
                tries += 1
                self.color=COLOR_ERROR
                time.sleep(5)

            # this board doesn't impliment rtc calibration so no point in trying.

    def set_tz(self,tz):
        self.tz=tz
        if tz=="America/New_York" or tz[:16]=="America/Indiana":
            self.__dstrule=dstrule.US_Eastern
        elif tz=="America/Chicago":
            self.__dstrule=dstrule.US_Central
        elif tz=="America/Denver":
            self.__dstrule=dstrule.US_Mountain
        elif tz=="America/Phoenix":
            self.__dstrule=dstrule.US_Arizona
        elif tz=="America/Los Angeles":
            self.__dstrule=dstrule.US_Pacific
        else:
            print(f"Unknown timezone \"{tz}\".")        

    @property
    def utc(self):
        l = time.localtime(time.time())        
        return f"{l.tm_year}-{l.tm_mon:02}-{l.tm_mday:02}T{l.tm_hour:02}:{l.tm_min:02}:{l.tm_sec:02}+00:00"

    @property
    def pretty_time(self):
        if not self.__dstrule:
            print("Timezone not set.")
            self.color=COLOR_UNCERTAIN
            return TIME_UNKNOWN

        try:
            l = self.__dstrule.localtime(time.time())
        except OverflowError:
            print("\nClock too early!")
            self.color=COLOR_UNCERTAIN
            return TIME_UNKNOWN

        if not self.twentyfour:
            if l.tm_hour > 12:
                s = f"{l.tm_hour-12}{self.delim}{l.tm_min:02}"
            elif l.tm_hour == 0:
                s = f"12{self.delim}{l.tm_min:02}"
            else:
                s = f"{l.tm_hour}{self.delim}{l.tm_min:02}"
        else:
            s = f"{l.tm_hour}{self.delim}{l.tm_min:02}"
        
        return s

    @property
    def isotime(self):
        if not self.__dstrule:
            print("Timezone not set.")
            self.color=COLOR_UNCERTAIN
            return ""

        try:
            l = self.__dstrule.localtime(time.time())        
        except OverflowError:
            print("Clock is way too far in the past.")
            self.color=COLOR_UNCERTAIN
            return ""

        if l.tm_isdst:
            tzoffset=self.__dstrule.altzone // 3600
        else:
            tzoffset=self.__dstrule.timezone // 3600

        return f"{l.tm_year}-{l.tm_mon:02}-{l.tm_mday:02}T{l.tm_hour:02}:{l.tm_min:02}:{l.tm_sec:02}-{tzoffset}:00"


    @property
    def today(self):
        return self.isotime[:10]
    
    @property
    def minute(self):
        return time.localtime(time.time()).tm_min

    @property
    def hour(self):
        return self.__dstrule.localtime(time.time()).tm_hour
    
    def uncertain(self):
        self.color = COLOR_UNCERTAIN
        print("Clock not certain.")

    

    def wait(self):

        if not self.__dstrule:
            print("Timezone not set.")
            time.sleep(1)
            return

        print(f"{self.isotime} Waiting for the minute to change.")

        t = time.localtime(time.time())
        s = t.tm_sec
        m = t.tm_min

        print(f"Waiting from :{s:02} to :59")
        time.sleep(59-s)

        print("Burning the last second.")
        # last < 1 second, spin-wait so we are very precise
        while time.localtime(time.time()).tm_min == m:
            pass
        
        print(self.isotime)


