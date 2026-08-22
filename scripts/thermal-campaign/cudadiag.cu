// Minimal CUDA health probe.
//
// Written on 2026-07-22 to prove that a CUDA failure was host-wide rather
// than an ollama bug: after the Windows VM releases the RTX 4070, NVML
// (nvidia-smi) keeps working while every CUDA entry point returns 999
// "unknown error". Run it after any VFIO round-trip to tell the two apart.
#include <cuda_runtime.h>
#include <stdio.h>

int main(void) {
    int n = -1;
    cudaError_t e;

    e = cudaGetDeviceCount(&n);
    printf("cudaGetDeviceCount: %s (count=%d)\n", cudaGetErrorString(e), n);
    e = cudaSetDevice(0);
    printf("cudaSetDevice(0):   %s\n", cudaGetErrorString(e));
    e = cudaFree(0);
    printf("cudaFree(0) [init]: %s\n", cudaGetErrorString(e));

    void *p = 0;
    e = cudaMalloc(&p, 1024 * 1024);
    printf("cudaMalloc(1MB):    %s\n", cudaGetErrorString(e));

    size_t f = 0, t = 0;
    e = cudaMemGetInfo(&f, &t);
    printf("cudaMemGetInfo:     %s (free=%zu MB total=%zu MB)\n",
           cudaGetErrorString(e), f >> 20, t >> 20);

    return (n > 0) ? 0 : 1;
}
