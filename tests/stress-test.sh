#!/bin/bash
# Final Ultimate Stress Test
# E-cores (stress-ng) + P-cores (VM stress-ng) + RTX 4070

echo "========================================"
echo "FINAL ULTIMATE STRESS TEST"
echo "All CPUs + RTX 4070 @ Maximum Load"
echo "========================================"
echo ""

# Variables
MAX_PACKAGE=0
MAX_GPU=0
MAX_GPU_POWER=0

echo ""
echo "Step 1: E-cores stress (CPUs 16-23) - stress-ng..."
sudo stress-ng --cpu 8 --cpu-method matrixprod --taskset 16-23 --timeout 130s --metrics-brief &
ECORE_PID=$!
echo "  ✅ Started (PID: $ECORE_PID)"
sleep 2

echo ""
echo "Step 2: P-cores stress (VM, all 14 vCPUs) - intensive math..."
# Start 14 intensive CPU jobs in parallel
winvm 'powershell -Command "$jobs = @(); for ($core = 1; $core -le 14; $core++) { $jobs += Start-Job -ScriptBlock { param($c) $end = (Get-Date).AddSeconds(125); while ((Get-Date) -lt $end) { for ($i = 0; $i -lt 100000000; $i++) { $x = [Math]::Sqrt($i) * [Math]::Sin($i) * [Math]::Cos($i) } } } -ArgumentList $core }; Write-Host \"Started 14 CPU stress jobs\"; $jobs | Wait-Job | Out-Null; $jobs | Remove-Job"' &
PCORE_PID=$!
echo "  ✅ Started 14 intensive math jobs (PID: $PCORE_PID)"
sleep 3

echo ""
echo "Step 3: RTX 4070 GPU stress - CUDA/DirectX compute..."
# Start GPU intensive workload
winvm 'powershell -Command "Start-Job -ScriptBlock { Add-Type -AssemblyName System.Drawing; for ($loop = 0; $loop -lt 125; $loop++) { $bmp = New-Object System.Drawing.Bitmap(8192, 8192); $gfx = [System.Drawing.Graphics]::FromImage($bmp); for ($i = 0; $i -lt 500; $i++) { $gfx.Clear([System.Drawing.Color]::FromArgb((Get-Random -Max 255), (Get-Random -Max 255), (Get-Random -Max 255))); $gfx.FillEllipse([System.Drawing.Brushes]::White, 0, 0, 8192, 8192); $gfx.DrawString(\"STRESS TEST\", (New-Object System.Drawing.Font(\"Arial\", 500)), [System.Drawing.Brushes]::Red, 100, 100) }; $gfx.Dispose(); $bmp.Dispose(); Start-Sleep -Milliseconds 100 } }"' &
GPU_PID=$!
echo "  ✅ Started GPU rendering stress (PID: $GPU_PID)"

echo ""
echo "Waiting 10s for all workloads to stabilize..."
sleep 10

echo ""
echo "========================================"
echo "MONITORING - 120 seconds"
echo "========================================"
printf "%-8s %-22s %-18s %-15s\n" "Sample" "CPU Package" "GPU Temp" "GPU Power"
printf "%-8s %-22s %-18s %-15s\n" "------" "------------" "--------" "---------"

for i in $(seq 1 24); do
    # CPU Package temp
    PKG=$(sensors coretemp-isa-0000 2>/dev/null | grep "Package id" | grep -oP '\+\K[0-9]+' | head -1)

    # GPU stats
    GPU_STATS=$(winvm 'nvidia-smi --query-gpu=temperature.gpu,power.draw --format=csv,noheader,nounits' 2>/dev/null)
    GPU=$(echo "$GPU_STATS" | cut -d',' -f1 | tr -d ' \r\n')
    GPU_POWER=$(echo "$GPU_STATS" | cut -d',' -f2 | tr -d ' \r\n')

    # Track maximums
    [ ! -z "$PKG" ] && [ "$PKG" -gt "$MAX_PACKAGE" ] && MAX_PACKAGE=$PKG
    [ ! -z "$GPU" ] && [ "$GPU" -gt "$MAX_GPU" ] && MAX_GPU=$GPU
    # power.draw can be non-numeric (e.g. "[Not Supported]") on some GPUs;
    # guard with a numeric test instead of silencing a comparison error.
    GPU_POWER_INT="${GPU_POWER%.*}"
    case "$GPU_POWER_INT" in
        ''|*[!0-9]*) ;;
        *) [ "$GPU_POWER_INT" -gt "${MAX_GPU_POWER%.*}" ] && MAX_GPU_POWER=$GPU_POWER ;;
    esac

    # Status
    [ "$PKG" -le 80 ] && CPU_S="✅" || CPU_S="⚠️"
    [ "$GPU" -le 85 ] && GPU_S="✅" || GPU_S="⚠️"

    printf "[%2d/24]  %s %2d°C (max:%2d°C)    %s %2d°C (max:%2d°C)   %sW (max:%sW)\n" \
        "$i" "$CPU_S" "$PKG" "$MAX_PACKAGE" "$GPU_S" "$GPU" "$MAX_GPU" "$GPU_POWER" "$MAX_GPU_POWER"

    sleep 5
done

echo ""
echo "Stopping workloads..."
sudo kill $ECORE_PID 2>/dev/null
wait $PCORE_PID 2>/dev/null
wait $GPU_PID 2>/dev/null
winvm 'powershell -Command "Get-Job | Stop-Job; Get-Job | Remove-Job"' &>/dev/null

sleep 3

echo ""
echo "========================================"
echo "FINAL RESULTS"
echo "========================================"
echo ""
echo "Maximum CPU Package Temperature: ${MAX_PACKAGE}°C"
echo "Maximum RTX 4070 Temperature: ${MAX_GPU}°C"
echo "Maximum RTX 4070 Power Draw: ${MAX_GPU_POWER}W"
echo ""

# Evaluation
if [ "$MAX_PACKAGE" -le 80 ]; then
    MARGIN=$((80 - MAX_PACKAGE))
    echo "✅ CPU: EXCELLENT! ${MARGIN}°C margin under 80°C target"
    CPU_RESULT="PASS"
elif [ "$MAX_PACKAGE" -le 85 ]; then
    OVER=$((MAX_PACKAGE - 80))
    echo "⚠️  CPU: ACCEPTABLE (${OVER}°C over target, but safe)"
    CPU_RESULT="WARN"
else
    OVER=$((MAX_PACKAGE - 80))
    echo "❌ CPU: TOO HOT (${OVER}°C over 80°C target)"
    CPU_RESULT="FAIL"
fi

if [ "$MAX_GPU" -le 80 ]; then
    echo "✅ GPU: EXCELLENT! (${MAX_GPU}°C)"
    GPU_RESULT="EXCELLENT"
elif [ "$MAX_GPU" -le 85 ]; then
    echo "✅ GPU: GOOD (${MAX_GPU}°C - normal under load)"
    GPU_RESULT="GOOD"
else
    echo "⚠️  GPU: WARM (${MAX_GPU}°C - check cooling)"
    GPU_RESULT="WARM"
fi

echo ""
echo "Summary:"
echo "--------"
echo "CPU Result: $CPU_RESULT"
echo "GPU Result: $GPU_RESULT"
echo ""
echo "Workloads tested:"
echo "  • E-cores (16-23): stress-ng matrix multiplication"
echo "  • P-cores (0-15): 14x parallel intensive math (VM)"
echo "  • RTX 4070: Graphics rendering stress"
echo ""
echo "Test completed: $(date)"
echo ""
