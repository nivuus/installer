// Sustained cuBLAS SGEMM load, the thermal stressor for the RTX 4070.
// Deliberately compute-bound rather than bursty: the campaign needs a steady
// power draw so the case reaches thermal equilibrium.
#include <cublas_v2.h>
#include <cuda_runtime.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int main(int argc, char **argv) {
    int seconds = (argc > 1) ? atoi(argv[1]) : 60;
    const int N = 8192;                       // ~1.1 TFLOP per GEMM, 805 MB total
    const size_t bytes = (size_t)N * N * sizeof(float);

    float *A, *B, *C;
    if (cudaMalloc(&A, bytes) != cudaSuccess ||
        cudaMalloc(&B, bytes) != cudaSuccess ||
        cudaMalloc(&C, bytes) != cudaSuccess) {
        fprintf(stderr, "cudaMalloc failed: %s\n",
                cudaGetErrorString(cudaGetLastError()));
        return 1;
    }
    cudaMemset(A, 1, bytes);
    cudaMemset(B, 1, bytes);

    cublasHandle_t h;
    if (cublasCreate(&h) != CUBLAS_STATUS_SUCCESS) {
        fprintf(stderr, "cublasCreate failed\n");
        return 1;
    }

    const float alpha = 1.0f, beta = 0.0f;
    time_t start = time(NULL);
    long iters = 0;

    while (time(NULL) - start < seconds) {
        cublasSgemm(h, CUBLAS_OP_N, CUBLAS_OP_N, N, N, N,
                    &alpha, A, N, B, N, &beta, C, N);
        cudaDeviceSynchronize();
        iters++;
    }

    double tflops = (2.0 * N * N * (double)N * iters) / (seconds * 1e12);
    printf("iters=%ld sustained=%.1f TFLOPS\n", iters, tflops);

    cublasDestroy(h);
    cudaFree(A); cudaFree(B); cudaFree(C);
    return 0;
}
