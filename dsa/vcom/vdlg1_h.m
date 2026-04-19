% vdlg1_h.m 
% Header file for vdlg_1.mi
% Dick Benson, DSP Technology

%  handle index definitions 
%define
   vrng      = 1;          % first control with state storage
   chenck    = vrng+1;
   cplckpb   = chenck+1;
   dbref     = cplckpb+1;  % was called spare1, now 0dB ref for vsa   c on vsa
   euckpb    = dbref+1;    
   offset    = euckpb+1;   % offset slider                    c
   euval     = offset+1;   % use this index to store eu value (not string)
   chname    = offset+1;   % S2 string storage
   eua       = chname+1;   %                                  c
   eub       = eua+1;      % last control with state storage  c
   
   chsel     = eub+1;      % channel select pop up 
   dl1       = chsel+1;          
   led1      = dl1+1;
   eublbl    = led1+1;     %                                  c 
   chlbl     = eublbl+1;
   dbreflbl  = chlbl+1;    %                                  c  on vsa
   titletxt  = dbreflbl+1; % leave at last position
%end_define





