% vi_file.m
% DSPT SigLab Measurement Storage file definitions
% Dick Benson DSP Technology  
%
% Each VI will have data storage associated with control states and
% labels. Beyond this VI specific state storage, a subset of the 
% following standardized arrays/scalars define measurements that 
% have been made and stored.
% Fear not, all the measurements defined by this file will not be
% present simultaneously. 


% ******** SYSTEM ******************************************************
% SystemClk    represents the fundamental system sampling clock. The 
%              default value for this is 51200 Hz on 20-x2 and 128000 on 50-21 
% size         real, scalar
% example:
  SystemClk = 51200;

% ******* ACQUISITION **************************************************
% SampleRate   represents actual sampling rate (post decimation) of
%              input data stream.
% size   :     real, scalar
% example:
  SampleRate = SystemClk/20; % system sampling clock after  
                             % decimate by 20 filter
  
% CenterFreq   represents the center frequency selected when the 
%              measurement was made. This relates to measurements made 
%              with the ZOOM bandpass filtering enabled. If it == 0.0,
%              we have a non zoomed measurement. If it is anything else,
%              the measurement used ZOOM. Time Domain data will be real
%              when ZOOM is off, and complex when ZOOM is on.
% size   :     real, scalar
% example:
  CenterFreq = 0.0; 

% ChanStat     define channels involved with measurements with state of 
%              Engineering Units: 0=off 1=on, and the value of the EU.
%              Also contains the overload status of the channel when the 
%              measurement was completed; 0=OK, 1= Overloaded.
%              Used with both single and two channel measurements.
% size   :     real, 1..NCH x 5 
%              (NCH=# of channels needed for measurements stored)
% example:     channel  eu on/off    eu value    ovldstatus  Vrng
  ChanStat   =    [1,     0,         1.0,           0,       10
                   2,     0,         1.0,           0,        5
                   5,     1,         0.1234,        0,        2.5
                  11,     1,         5.4321,        0,        10];

% ChanLabel    contains channel labels, 
%              End of the field is delimited by the ~ (tilde) character. 
% size   :     NCH x 20 
% example:
  ChanLabel =  ['Channel 1~          ';
                'Power Ch2~          ';
                'Torque Ch5~         ';
                'Current Ch11~       '];

% EULabel      contains Engineering Unit labels. 
%               
% size   :     NCH x 20 
% example:
  EULabel   =  ['Gs~                 ';
                'Watts~              ';
                'Inch Pounds~        ';
                'Amps~               ']; 

% Navg         represents how many averages were used to produce the 
%              measurement results. If Navg=0, no averaging whatsoever
%              was used.
% size   :     real, scalar
% example:
  Navg      =  100;
  

% ********* OUTPUT ****************************************************

% If any of these are vectors a 1:1 channel / index relationship
% is assumed. e.g element 3 corresponds to output channel 3

 
% OutCenterFreq represents the output modulator center frequency.
%               If it == 0.0, we have a non translated output. 
%               The translated output is double-sided unlike the input
%               processing which produces a single-sided spectrum with 
%               a complex data stream.


%  OutF1        frequency of repetative function
%  OutF2        frequency#2 in two-tone
%  OutRatio     amplitude ration in two-tone
%  
%  StartF       start freq for chirp
%  StopF        stop  freq for chirp


%  OutIfac      interpolation factor (OutBurst functions only)

%  OutTon       on time in seconds for 'OutBurst' functions

  
%  OutToff      off time in seconds for 'OutBurst' functions
  
%  OutFunction  string describing function selected
%               sine1    single sine 
%               sine2    two tone
%               square
%               triangle
%               sawtooth
%               impulse
%               chirp
%               rand
%               arb


%  The following are related to arbitrary waveform output
%  Arb_Clk       set to SystemClk = 51200 Hz.
%  Arb_Interp_Index  = Index into interpolation table 
%  Arb_Data
%  Arb_Text
% *********TIME DOMAIN DATA *******************************************


% TrigDelay    defines the trigger delay in samples. If it is < 0, 
%              we have pre-trigger, if > 0 we have post-trigger.
% size         real, scalar
% example:
  TrigDelay  =  -20;
  

% Tvec         vector defining the sampling time instants in seconds. 
%              This vector is normally provided with time domain 
%              measurements, although it is easy to compute. 
% size         real, TDL x 1
%                    TDL = Time Domain record Length
% example:
  Tvec      = (TrigDelay:1:79)'*(1/SampleRate);
                               % the acquisition had 20 samples
                               % of pre-trigger, with a buffer
                               % length of 100 samples (79-(-20)+1).
                               % TDL = 100;


% TimeMap     defines channel / data relationship for TimeIDat 
% size         real, 1..NTDI x 1
% example:
  TimeMap  =  [1;2;5];

% TimeDat      contains time domain measurements. The array is complex if
%              the data is acquired with ZOOM enabled (CenterFreq ~=0). 
% size:        real OR complex TDL x NTDI
% example:
  TimeDat  =  [sin(50*2*pi*Tvec),... % channel 1 given above TimeIMap 
               cos(25*2*pi*Tvec),... % channel 2
               100 * Tvec.*Tvec];    % channel 5 



% CorTvec      vector defining the time axis points of the Auto and 
%              Cross Correlation measurements in seconds. 
%              This vector is always provided with correlation 
%              measurements. 
% size         real, CL x 1
%                    CL = Correlation record Length


% AutoCorMap   defines channel / data relationship for AutoCorDat 
% size         real, 1..NTAC x 1
% example:
  AutoCorMap=  [2;11];  

% AutoCorDat   contains auto-correlation measurements.   
% size         real, CL x NTAC



% CrossCorMap  defines the channels used to measure the stored 
%              cross correlation functions.
%              Each of these mesurements requires a 'reference channel'
%              and a 'response channel'. 
% size   :     real, 1..NCC x 2 
%                      (NCC = # of cross correlation measurements)
% example: 
%               resp.   ref
  CrossCorMap = [2,      1;
                 5,      1;
                 11,     5];

% CrossCorDat  contains cross-correlation measurements. This is a two
%              channel measurement and therefore requires the 
%              information in CrossCorMap to determine which channels 
%              participated in the measurement.
% size:        real, CL x NXM



% ImpulseMap   defines the channels used to measure the stored 
%              Impulse Response functions.
%              Each of these mesurements requires a 'reference channel'
%              and a 'response channel'. 
% size   :     real, 1..NIM x 2 
%                      (NIM = # of impulse response measurements)
% example: 
%               resp.   ref
  ImpulseMap =  [2,      1;
                 5,      1;
                 11,     5];

% ImpulseDat   contains impulse response measurements. This is a two
%              channel measurement and therefore requires the 
%              information in ImpulseMap to determine which channels
%              participated in the measurement.
% size:        real, TDL x NIM


% *********FREQUENCY DOMAIN DATA **************************************
%
% Fvec         defines the frequency values in Hz for the measurements 
%              in the arrays: AspecDat XferDat CohDat CspecDat.
%              This vector is always provided with frequency domain 
%              measurements. The separation of the frequency points
%              does not have to be uniform but the data is assumed to
%              be increasing monotonically: Fvec(n+1) > Fvec(n).
% size         real, FDL x 1;      FDL = Frequency Domain buffer Length 
% example:
 if CenterFreq == 0.0, 
     Fvec = (0:1:1024/2.56)*(SampleRate/1024);
 else
     Fvec = CenterFreq + (-512/2.56:1:512/2.56)*(SampleRate/1024);
 end;

% Uniformflg   indicates that the Fvec has a uniform element spacing
%              if == 1 it is uniform , if == 0 non-uniform 
% size         real, scalar
% example:
  UniformFlg  = 1;


% FFTWindow    defines the window used (if any) in the spectral 
%              arithmetic. Two size options are possible. The first is
%              NPTx1 which specifies the window that is NPT long applied
%              (convolved in the frequency domain) to every 
%              spectral calculation. NPT is always < FDL 
% size1        real, NPTx1
%              The second size option would be NPTxNCH which allows 
%              a different window to be used on each channel. 
%              (this is a rare conditin)
% size2        real, NPT x NCH

% WindowName   contains the commonly reffered to names of the windows in
%              FFTWindow. 
%              End of the field is delimited by the ~ (tilde) character. 
% size   :     1..NCH x 20  or 1x20 if window is common to all measurements
% example:
  WindowName = ['Box Car ~           ';
                'Hanning~            ';
                'Flat Top~           ';
                'Hamming~            ']; 

% WindowCor    contains correction factor for power and window number 
%              defined by avgdef_h.m
% size  :      1..NCH x 2 or  1x2 if same window for all measurements
% example:
  WindowCor  = [0.2617872274,3];

% AspecMap     defines channel / data relationship for AspecDat 
% size         real, 1..NAS x 1
% example:
  AspecMap =   [5;11];   
 
% AspecDat     contains auto-spectrum measurements from averaging Navg 
%              ensembles. Fvec defines the frequency of each spectral 
%              component. This is a single channel measurement and the
%              data in AspecMap determines the channel/data pairs.
% size         real,    FDL x NAS

% XferMap      defines the channels used to measure the transfer 
%              functions in XferDat. Each of these measurements requires
%              a 'reference channel' and a 'response channel'. 
% size   :     real, 1..NXF x 2 
%                      (NXF = # of transfer function measurements)
% example: 
%               resp.   ref
  XferMap =     [2,      1;
                 5,      1;
                 11,     5];


% XferDat      contains transfer function measurements at the
%              frequencies defined by f_vec. This is a two channel
%              measurement and the data in XferMap determines which
%              channel was the reference and which was the reponse.
% size         complex, FDL x NXF


% CohMap       defines the channels used to measure the Coherence 
%              functions in CohDat. Each of these measurements requires
%              a 'reference channel' and a 'response channel'. 
% size   :     real, 1..NCHF x 2 
%                       (NCHF = # of coherence function measurements)
% example: 
%               resp.   ref
  CohMap  =     [2,      1;
                 5,      1;
                 11,     5];

% CohDat       contains coherence function measurements at the
%              frequencies defined by Fvec. This is a two channel
%              measurement and the data in CohMap  determines which
%              channel was the reference and which was the reponse.
% size         complex, FDL x NCHF

 
% CspecMap     defines the channels used to measure the cross spectrum
%              functions in CspecDat. Each of these measurements
%              requires a 'reference channel' and a 'response channel'. 
% size   :     real, 1..NCSF x 2 
%                      (NCSF= # of cross spectrum function measurements)
% example: 
%               resp.   ref
  CspecMap  =   [2,      1;
                 5,      1;
                 11,     5]; 

% CspecDat     contains cross-spectrum function measurements at the
%              frequencies defined by Fvec. This is a two channel
%              measurement and the data in CspecMap determines which
%              channel was the reference and which was the reponse.
% size         complex, FDL x NCSF

















