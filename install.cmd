@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
