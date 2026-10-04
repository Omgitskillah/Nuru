#!/bin/zsh
# time | serve %CPU | serve footprint (top MEM) | GPU device util % | GPU in-use mem MB | swap used MB | free mem %
P=$(pgrep -f "ollama serve" | head -1)
while true; do
  t=$(python3 -c 'import time;print(f"{time.time():.2f}")')
  cpu=$(ps -o %cpu= -p $P | tr -d ' ')
  mem=$(top -l 1 -pid $P -stats mem | tail -1 | tr -d ' ')
  g=$(ioreg -r -d 1 -w 0 -c IOAccelerator | grep -o '"PerformanceStatistics" = {[^}]*}')
  gu=$(echo $g | grep -o '"Device Utilization %"=[0-9]*' | cut -d= -f2)
  gm=$(echo $g | grep -o '"In use system memory"=[0-9]*' | cut -d= -f2)
  sw=$(sysctl -n vm.swapusage | awk '{print $6}' | tr -d M)
  fr=$(memory_pressure -Q | grep -o '[0-9]*%' | tr -d %)
  echo "$t $cpu $mem $gu $((gm/1048576)) $sw $fr"
done
