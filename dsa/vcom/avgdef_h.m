% avgdef_h.m
% Header file with averaging mode definitions 
% and analysis window definitions
% Dick Benson, DSP Technology  

%define

  AVGSTRc=['Add|Expon|Peak|Adaptive|Time']; 
  ADDc      = 1;
  EXPc      = 2;
  PKc       = 3;
  ADAPTc    = 4;
  TIMEAVGc  = 5;
  
  
  OVLPSTRc  = 'No Overlap|50% Overlap|Max Overlap';
  %               1           2          3
  % overlap code = (sel-1)*50 = [0,50,100]
  
  % windowing stuff is next....
  
  
  % WINSTRc list has 1:1 correspondence with window defs in siglab.txt
  %                   0=Boxcar    5=Potter210   10=BHarris61   14=Force20
  %                   1=Hanning   6=Potter310   11=BHarris67   15=Exp.1  
  %                   2=FlatTop   7=Hamming     12=BHarris74   16=Exp.01 
  %                   3=Flat301   8=Blackman    13=BHarris92
  %                   4=Flat201   9=Exact-Bl 


  BOXc       =0;
  HANNc      =1;
  F20c       =14;
  EXPP1c     =15;
  EXPP01c    =16;
  USEREXPc   =17;
  USERFORCEc =18; 

  % cover structurally regular non modal stuff 1st
  WINSTRc=['Boxcar |Hanning|FlatTop|Flat301|Flat201|Potter210|Potter310|',...
           'Hamming|Blackman|Exact-Bl|BHarris61|BHarris67|BHarris74|BHarris92'];

  MODALSTARTc= 15; % popup selector starts @ 1 not 0
  BOX_EXPP1c = 15;
  BOX_EXPP01c= 16;
  F20_EXPP1c = 17;
  F20_EXPP01c= 18;
  USERDEFWINc= 19; % added 10/12/98
  %               15       16         17        18           19
  MODALWINc=['|Box_Exp.1|Box_Exp.01|F20_Exp.1|F20_Exp.01|User Modal'];                 

  % Power Correction Factors for the non modal analysis windows, put 0.99 in for modal stuff
  PWRCORc=[1.0000000000,...
           0.6666666667,...
           0.2617872274,...
           0.2920416990,...
           0.3378558539,...
           0.5642952975,...
           0.4947506823,...
           0.7311777141,...
           0.5791201619,...
           0.5904311459,...
           0.6208287665,...
           0.5852957066,...
           0.5583575867,...
           0.4989141365,...        % slightly tweaked [PM] 29-Oct-95
           0.99,....
           0.99,...
           0.99,...
           0.99,...
           0.99]; 
%end_define

% window convolution kernals 
winconv  = [...
           1.000000, 0.,         0.,         0.,          0.        %%  1: Boxcar    
           1.000000, -.5,        0.,         0.,          0.        %%  2: Hanning   
           1.000000, -.97017900, .653919000, -.201947000, .017552   %%  3: FlatTop   
           0.9994484, -.95572800, .539289000, -.091581000, 0.       %%  4: Flat301   
           0.9990280, -.92575200, .351960000, 0.,          0.       %%  5: Flat201   
           1.000000, -.61129000, .111290000, 0.,          0.        %%  6: Potter210 
           1.000000, -.68498800, .202701000, -.017712700, 0.        %%  7: Potter310 
           1.000000, -.42875200, 0.,         0.,          0.        %%  8: Hamming   
           1.000000, -.59523810, .095238095, 0.,          0.        %%  9: Blackman  
           1.000000, -.58201156, .090007307, 0.,          0.        %% 10: Exact-Bl  
           1.000000, -.54898908, .063135301, 0.,          0.        %% 11: BHarris61 
           1.000000, -.58780096, .093589774, 0.,          0.        %% 12: BHarris67 
           1.000000, -.61793520, .116766542, -.002275157, 0.        %% 13: BHarris74 
           1.000000, -.68054355, .196905923, -.016278746, 0.        %% 14: BHarris92 
           0.2,      0.,         0.,         0.,          0.        %% 15: Force20   
           0.1,      0.,         0.,         0.,          0.        %% 16: Exp.1     
           0.01,     0.,         0.,         0.,          0.        %% 17: Exp.01        
           0.01,     0.,         0.,         0.,          0.        %% 18: Exp.User   
           0.2,      0.,         0.,         0.,          0.        %% 19: Force.User    
           1.000000, -.5,        0.,         0.,          0. ];     %% 20: Frq.User  
  










