import amazingdata_macos as ad


ready = ad.login()
print("Gateway ready:", ready)
calendar = ad.BaseData().get_calendar(market="SH")
print(calendar[-10:])
