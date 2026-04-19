% hdlg2_h.m
% Header file for hdlg_2.mi
% Dick Benson, DSP Technology

%  handle index definitions 
%define
%  set up indexes into handle array HH2_ 
   tselpu1   = 1;          % leave tselpu1 at begining    c
   filtck    = tselpu1+1;  % filter on/off ck pb          c3
   delay     = filtck+1;   % trig delay slider            c
   thrpu2    = delay+1;    % theshold pop up              c3
   slopeck   = thrpu2+1;   % slope pos/neg ck pb          c3 
   armck     = slopeck +1; % Manual / Auto Arm Button     c2
   modepu3   = armck +1;   % trigger mode pop up
   numin     = modepu3+1;  % number of inputs (not a control)
   numout    = numin+1;    % number of outputs (not a control)
       
  
   %end of S1 state storage

   dlgfrm    =  modepu3+1;  % dialog frame

   armled    =  dlgfrm +1; %                              c2 
   filtled   =  armled +1; %                              c3
   armmenu   =  filtled+1; %                              c2 , for Volvo
   titletxt  =  armmenu+1;  % leave at last position
                                                % c = conditional
                                                % visibility 
%end_define










