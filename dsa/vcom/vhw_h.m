% vhw_h.m
% Header file with definitions pertaining to 20-22 / 50-21 system characteristics
% Dick Benson, DSP Technology

% Input related....
  BLK_MINc   = 64;     % Minimum & Maximum number of points in data block                  
  BLK_MAXc   = 8192;
  BLK_MAXZMc = 4096;   % zoom max frame size 10/10/95
  DLY_MINc   = -100;   % max pretrigger delay 
  DLY_MAXc   =  100;   % max posttrigger delay 
  V2P5c      =  3;     % range three and up are <= 2.5V (Offset change)
  VINOF2P5c  = 2.5;
  VINOFMAXc  =  8;     % range 5 and 10 have +/- 8 max offset 
  VMAXc      = 10;     % 10 Volts max input 
  MAXCHANc   = 16;     % maximum number of input channels 
  MINZMINDEXc= 3;      % need a decimation selection beyond this for zoom
             %       1   2    3    4   5   6     7    8     9    10    11    12      13 % Decimation Index
  DECIMATE_TBLc=    [1,  2,   4,  10, 20, 40,  100, 200,  400, 1000, 2000, 4000,  10000];
  % 20-XXBW      XX  20  10   5   2   1   0.5  0.2  0.1  0.05  0.02  0.01  0.005  0.002    
  % 50-21BW=     50  20  10   5   2   1   0.5  0.2  0.1  0.05  0.02  0.01  0.005     XX]
  DECIMATE_50Kc=[ 1, 2.5, 5, 10, 25, 50,  100, 250, 500, 1000, 2500, 5000, 10000 ];
  L50Kc        = 13; % number of entries in 50 K table
  L20Kc        = 13; % ditto             in 20 K table
% Output Related 

  VPPMAXc      = 10.0;  % max peak output level in Volts
% VARBMAXc     = 5.0;   % max peak output level for arb 10/10/95
  VARBMAXc     = 10.0;  % max peak output level for arb 6/18/96
  VARB5021c    = 5.0;
  VRMSMAXc     = 2.5;   % max RMS output for random 
  MAXBUFFLENc  = 51200; % max # of samples in burst random
  MINBUFFLENc  = 16;    % min # of samples
  CHIRPMAXc    = 2e9;   % maximum samples in chirp sequence 
  VOFSMAXc     = 10;    % max output offset
           %   1 2 3  4  5  6  7   8   9   10   11   12   13 % Interp Index
  INTERP_TBLc=[1,2,4,10,20,40,100,200,400,1000,2000,4000,10000];
               % Available Interpolations

% System related 
  OVS_FACc   = 2.56;   % 2.56:1 Oversampling, Fs=2.56*Bandwidth
  TB51200c   = 51200;  % 20-xx primary sampling rate (TimeBase)
  TB50_21c   = 128000; % 50-21 primary sampling rate
  PREXISTc   = 328704; % to request pre-existing data per GLS

% Standard File related Constants
    CRESPc     = 1;     % response  column
    CREFc      = 2;     % reference column
  
    % in ChanStat array
    CHANc      = 1;     % channel number
    EU_ONc     = 2;     % Eng units
    EU_VALc    = 3;     % Value of EU
    OVLD_STATc = 4;     % Overload status
    VFSc       = 5;     % Volts full scale
    
