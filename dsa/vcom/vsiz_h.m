% vsiz_h.m
% Header file: uiobject size definitions in pixels
% Dick Benson, DSP Technology 
  
%define  
  HTXTc   = 16;        % text height 
  HTITLEc = 16;        % title text height 
  LHOc    = 5;         % horizontal offset for labels  
  VSc     = 25;        % vertical control spacing 
  VOc     = 4;         % small vertical offset
  
  HSLDc   = 17;        % slider height 
  SSP1c   = 17;        % slider spacing #1     
  SSP2c   = 33;        % slider spacing #2
  
  
  HPBS1c  = 20;        % pushbutton height
  WPBS1c  = 42;        %            width
  PBS1c   = [WPBS1c,HPBS1c];% normal pushbutton size
  
  HCKS1c  = 20;
  WCKS1c  = 38;
  CKS1c   = [WCKS1c,HCKS1c]; % check button size (homebrew)
  
  HOKCc   = 20;
  WOKCc   = 32;             % width OK_CAN
  OK_CANc = [WOKCc,HOKCc];  % OK CANCEL pushbutton size
  
  WLDVc   = 8;          % width of vertical LED
  HLDVc   = 20;         % height of vertical LED
  LEDS1c  = [WLDVc,HLDVc];  % width / height of vertical LED
  
  WLDHc   = 19;
  HLDHc   = 6;
  LEDS2c  = [WLDHc,HLDHc];  % width / height of horizontal LED 
  
  HPU1c   = 21;          % for MATLAB 5
  
  SCSIMSGc = 'Exceeded Transfer Limit. Try reducing one or more of the following: frame size; active channels; active applications; OK ?'; 
  
%end_define 






