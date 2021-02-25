# coding=utf-8
from __future__ import absolute_import
import octoprint.plugin
from octoprint.util import RepeatedTimer, ResettableTimer
import re
import psutil
import sys
import os
from octoprint.events import Events, eventManager
noAccessPermissions = False
try:
	from octoprint.access import ADMIN_GROUP
	from octoprint.access.permissions import Permissions
except ImportError:
	noAccessPermissions = True
import subprocess
import json
import platform
import flask

class DashboardPlugin(octoprint.plugin.SettingsPlugin,
					  octoprint.plugin.StartupPlugin,
					  octoprint.plugin.AssetPlugin,
					  octoprint.plugin.TemplatePlugin,
					  octoprint.plugin.EventHandlerPlugin,
					  octoprint.plugin.SimpleApiPlugin):

	printer_message = ""
	extruded_filament = 0.0
	extruded_filament_arr = []
	extruder_mode = ""
	cpu_percent = 0
	cpu_temp = 0
	cpu_freq = 0
	virtual_memory_percent = 0
	disk_usage = 0
	layer_times = []
	layer_labels = []
	cmd_commands = []
	cmd_timers = []

	if noAccessPermissions == False:
		def get_additional_permissions(*args, **kwargs):
			return [
				dict(key="ADMIN",
					name="Admin access",
					description="Allows modifying or adding shell commands",
					roles=["admin"],
					dangerous=True,
					default_groups=[ADMIN_GROUP])
			]

	def psUtilGetStats(self):
		if platform.system() == "Linux":
			temp_sum = 0
			thermal = psutil.sensors_temperatures(fahrenheit=False)
			if "cpu-thermal" in thermal: #RPi
				self.cpu_temp = int(round((thermal["cpu-thermal"][0][1])))
			elif "cpu_thermal" in thermal: #RPi Alternative
				self.cpu_temp = int(round((thermal["cpu_thermal"][0][1])))
			elif 'soc_thermal' in thermal: #BananaPi
				self.cpu_temp=int(round(float(thermal['soc_thermal'][0][1])*1000))
			elif 'coretemp' in thermal: #Intel
				for temp in range(0,len(thermal["coretemp"]),1):
					temp_sum = temp_sum+thermal["coretemp"][temp][1]
				self.cpu_temp = int(round(temp_sum / len(thermal["coretemp"])))
			elif 'w1_slave_temp' in thermal: #Dallas temp sensor fix
				tempFile = open("/sys/class/thermal/thermal_zone0/temp")
				cpu_val = tempFile.read()
				tempFile.close()
				self.cpu_temp = int(round(float(cpu_val)/1000))
			self.cpu_percent = str(psutil.cpu_percent(interval=None, percpu=False))
			self.cpu_freq = str(int(round(psutil.cpu_freq(percpu=False).current, 0)))
			self.virtual_memory_percent = str(psutil.virtual_memory().percent)
			self.disk_usage = str(psutil.disk_usage("/").percent)

	def runCmd(self, cmdIndex):
		cmd = self.cmd_commands[cmdIndex].get("command")

		interval = float(self.cmd_commands[cmdIndex].get("interval"))

		process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)

		try:
			stdout, stderr = process.communicate(timeout=interval)
		except subprocess.TimeoutExpired:
			process.kill()
			self._logger.warn("cmd widget \"{0}\" ran for too long".format(cmd))
			stdout, stderr = process.communicate()

		if (sys.version_info > (3, 5)):
			# Python 3.5
			result = stdout.strip().decode('ascii')
			error = stderr.strip().decode('ascii')
		else:
			# Python 2
			result = stdout.strip()
			error = stderr.strip()

		if (error != ""):
			self._logger.warn("cmd widget ran with error: " + error)

		results = {"index": cmdIndex, "result": result}

		self._plugin_manager.send_plugin_message(self._identifier, dict(cmdResults=json.dumps(results)))

	def testCmd(self, cmd):
		process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)

		try:
			stdout, stderr = process.communicate(timeout=10.0)
		except subprocess.TimeoutExpired:
			process.kill()
			self._logger.warn("cmd widget test \"{0}\" ran for too long".format(cmd))
			stdout, stderr = process.communicate()

		if (sys.version_info > (3, 5)):
			# Python 3.5
			result = stdout.strip().decode('ascii') + stderr.strip().decode('ascii')
		else:
			# Python 2
			result = stdout.strip() + stderr.strip()

		results = {"result": result}

		self._plugin_manager.send_plugin_message(self._identifier, dict(cmdTest=json.dumps(results)))

	def updateCmds(self):
		for timer in self.cmd_timers:
			timer.cancel()

		del self.cmd_timers[:]

		index = 0
		for command in self.cmd_commands:
			if (command.get("enabled")):

				t = RepeatedTimer(float(command.get("interval")), self.runCmd, [index], run_first=True)
				t.start()
				self.cmd_timers.append(t)

			index += 1

	##~~ SimpleApiPlugin mixin
	def get_api_commands(self):
		return dict(
			testCmdWidget=["cmd"]
		)

	def on_api_command(self, command, data):
		if command == "testCmdWidget":
			if (noAccessPermissions or Permissions.PLUGIN_DASHBOARD_ADMIN.can()):
				self._logger.info("testCmdWidget called, cmd is {cmd}".format(**data))
				self.testCmd(data["cmd"])
			else:
				self._logger.info("testCmdWidget called, but without proper permissions")

	# ~~ StartupPlugin mixin
	def on_after_startup(self):
		self._logger.info("Dashboard started")
		self.cmd_commands = self._settings.get(["commandWidgetArray"])
		self.updateCmds()
		self.timer = RepeatedTimer(3.0, self.send_notifications, run_first=True)
		self.timer.start()


	def send_notifications(self):
		self.psUtilGetStats()
		self._plugin_manager.send_plugin_message(self._identifier, dict(cpuPercent=str(self.cpu_percent),
																		virtualMemPercent=str(self.virtual_memory_percent),
																		diskUsagePercent=str(self.disk_usage),
																		cpuTemp=str(self.cpu_temp),
																		cpuFreq=str(self.cpu_freq),
																		extrudedFilament=str( round( (sum(self.extruded_filament_arr) + self.extruded_filament) / 1000, 2) ),
																		layerTimes=str(self.layer_times),
																		layerLabels=str(self.layer_labels),
																		printerMessage=str(self.printer_message)))

	def on_event(self, event, payload):
		if event == "DisplayLayerProgress_layerChanged" or event == "DisplayLayerProgress_fanspeedChanged" or event == "DisplayLayerProgress_heightChanged":
			self._plugin_manager.send_plugin_message(self._identifier, dict(totalLayer=payload.get('totalLayer'),
																			currentLayer=payload.get('currentLayer'),
																			currentHeight=payload.get('currentHeightFormatted'),
																			totalHeight=payload.get('totalHeightFormatted'),
																			fanspeed=payload.get('fanspeed'),
																			lastLayerDuration=payload.get('lastLayerDuration'),
																			lastLayerDurationInSeconds=payload.get('lastLayerDurationInSeconds'),
																			averageLayerDuration=payload.get('averageLayerDuration'),
																			averageLayerDurationInSeconds=payload.get('averageLayerDurationInSeconds'),
																			changeFilamentTimeLeft=payload.get('changeFilamentTimeLeft'),
																			changeFilamentCount=payload.get('changeFilamentCount')))

		if event == "DisplayLayerProgress_layerChanged" and payload.get('lastLayerDurationInSeconds') != "-" and int(payload.get('lastLayerDurationInSeconds')) > 0:
			#Update the layer graph data
			self.layer_times.append(payload.get('lastLayerDurationInSeconds'))
			self.layer_labels.append(int(payload.get('currentLayer')) - 1)

		if event == "DisplayLayerProgress_feedrateChanged":
			feedrate = float(payload.get("feedrate")) / 60
			self._plugin_manager.send_plugin_message(self._identifier, dict(feedrate = feedrate))

		if event == "PrintStarted":
			del self.layer_times[:]
			del self.layer_labels[:]
			self. extruded_filament = 0.0
			del self.extruded_filament_arr[:]
			self._plugin_manager.send_plugin_message(self._identifier, dict(printStarted="True"))

		#if event == Events.FILE_SELECTED:
			#self._logger.info("File Selected: " + payload.get("file", ""))
			#TODO: GCODE analysis here


	##~~ SettingsPlugin mixin
	def get_settings_defaults(self):
		return dict(
			gaugetype="circle",
			fullscreenUseThemeColors=False,
			hotendTempMax="300",
			bedTempMax="100",
			chamberTempMax="50",
			temperatureTicks="0",
			showFan=True,
			showWebCam=False,
			showSystemInfo=False,
			showProgress=True,
			showTimeProgress=True,
			showLayerProgress=False,
			showHeightProgress=False,
			hideHotend=False,
			supressDlpWarning=False,
			showFullscreen=True,
			showFilament=True,
			showFilamentChangeTime=True,
			showLayerGraph=False,
			layerGraphType="normal",
			showPrinterMessage=False,
			showSensorInfo=False,
			showJobControlButtons=False,
			showFeedrate=False,
			cpuTempWarningThreshold="70",
			cpuTempCriticalThreshold="85",
			showTempGaugeColors=False,
			enableTempGauges=True,
			targetTempDeviation="10",
			useThemeifyColor=True,
			showCommandWidgets=False,
			disableWebcamNonce=False,
			disableWebcamLocal=False,
			commandWidgetArray=[dict(
					icon='command-icon.png',
					name='Default',
					command="echo 9V",
					enabled=False,
					interval="10")],
			enableDashMultiCam=False,
			_webcamArray=[dict(
				name='Default',
				url=octoprint.settings.settings().get(["webcam","stream"]),
				flipV=octoprint.settings.settings().get(["webcam","flipV"]),
				flipH=octoprint.settings.settings().get(["webcam","flipH"]),
				rotate=octoprint.settings.settings().get(["webcam","rotate90"]),
				disableNonce=False,
				streamRatio=octoprint.settings.settings().get(["webcam","streamRatio"]),
				)],
			defaultWebcam=0,
			dashboardOverlayFull=False,
			fsSystemInfo=True,
			fsTempGauges=True,
			fsFan=True,
			fsCommandWidgets=True,
			fsJobControlButtons=False,
			fsSensorInfo=True,
			fsPrinterMessage=True,
			fsProgressGauges=True,
			fsLayerGraph=False,
			fsFilament=True,
			fsWebCam=True,
			fsFeedrate=True,
			feedrateMax=400
		)

	def get_settings_restricted_paths(self):
		return dict(
			user=[["_webcamArray",], ["commandWidgetArray",]]
		)

	def on_settings_migrate(self, target, current):
		if (current == None or current < 1):
			tmpCmdArray = self._settings.get(["commandWidgetArray"])
			for cmd in tmpCmdArray:
				if not('enabled' in cmd):
					cmd['enabled'] = False
				if not('interval' in cmd):
					cmd['interval'] = 10
			self._settings.set(["commandWidgetArray"], tmpCmdArray)
			self._settings.save()

	def get_settings_version(self):
		return 1

	def on_settings_save(self, data):
		if (noAccessPermissions == False and Permissions.PLUGIN_DASHBOARD_ADMIN.can() == False):
			try:
				del data['commandWidgetArray']
			except:
				pass
		#self._logger.info(str(data))
		octoprint.plugin.SettingsPlugin.on_settings_save(self, data)
		self.cmd_commands = self._settings.get(["commandWidgetArray"])
		self.updateCmds()


	def get_template_configs(self):
		return [
			dict(type="tab", custom_bindings=True),
			dict(type="settings", custom_bindings=True)
			]

	##~~ AssetPlugin mixin
	def get_assets(self):
		return dict(
			js=["js/dashboard.js", "js/chartist.min.js", "js/fitty.min.js"],
			css=["css/dashboard.css", "css/chartist.min.css"],
			less=["less/dashboard.less"]
		)

	##~~ Softwareupdate hook
	def get_update_information(self):
		return dict(
			dashboard=dict(
				displayName="Dashboard Plugin",
				displayVersion=self._plugin_version,

				# version check: github repository
				type="github_release",
				user="j7126",
				repo="OctoPrint-Dashboard",
				current=self._plugin_version,

				stable_branch=dict(
					name="Stable",
					branch="master",
					comittish=["master"],
				),

				prerelease_branches=[
					{
					"name": "Release Candidate",
					"branch": "rc",
					"comittish": ["rc", "master"]
					}
				],

				# update method: pip
				pip="https://github.com/j7126/OctoPrint-Dashboard/archive/{target_version}.zip"
			)
		)

	def process_gcode(self, comm_instance, phase, cmd, cmd_type, gcode, *args, **kwargs):
		if not gcode:
			return

		elif gcode == "M117":
			if not cmd.startswith("M117 INDICATOR-Layer"):
				self.printer_message = cmd.replace("M117 ", "")
			else: return

		elif gcode in ("M82", "G90"):
			self.extruder_mode = "absolute"

		elif gcode in ("M83", "G91"):
			self.extruder_mode = "relative"

		elif gcode in ("G92"): #Extruder Reset
			if self.extruder_mode == "absolute":
				self.extruded_filament_arr.append(self.extruded_filament)
			else: return

		elif gcode in ("G0", "G1"):
			CmdDict = dict ((x,float(y)) for d,x,y in (re.split('([A-Z])', i) for i in cmd.upper().split()))
			if "E" in CmdDict:
				e = float(CmdDict["E"])
				if self.extruder_mode == "absolute":
					self.extruded_filament = e
				elif self.extruder_mode == "relative":
					self.extruded_filament += e
				else: return

		else:
			return





__plugin_name__ = "Dashboard"
__plugin_pythoncompat__ = ">=2.7,<4"

def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = DashboardPlugin()

	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.queued": __plugin_implementation__.process_gcode
	}
	if noAccessPermissions == False:
		__plugin_hooks__["octoprint.access.permissions"] = __plugin_implementation__.get_additional_permissions


	global __plugin_settings_overlay__
	__plugin_settings_overlay__ = dict(appearance=dict(components=dict(order=dict(tab=["plugin_dashboard",
																						"temperature",
																						"control",
																						"gcodeviewer",
																						"terminal",
																						"timelapse"]))))
