  function [sout,a,b,mfac]=sec2str(t1,mode,t2)
% function [sout,a,b,mfac]=sec2str(t1,mode,t2)
% Return string with xxs format (ns , ms , us etc) if only t1 arg
% Return Nano Seconds etc for use by axis if mode=axis.
% Dick Benson, DSP Technology

  if nargin == 1
    if abs(t1) < 1e-6, 
       sout = sprintf('%5.1fns',t1*1e9 ); 
    elseif abs(t1) < 1e-3, 
        sout = sprintf('%5.1fus',t1*1e6 ); 
    elseif abs(t1) < 1, 
        sout = sprintf('%5.1fms',t1*1e3 ); 
    elseif abs(t1) < 60, 
         sout = sprintf('%5.1fs',t1 ); 
    elseif abs(t1) < 3600
         sout = sprintf('%5.1fmin',t1/60); 
    elseif abs(t1) < 3600*60
         sout = sprintf('%5.1fhr',t1/3600); 
    else
        sout = sprintf('%7.1fsec',t1); 
  end; % if
 else
   if strcmp(mode,'axis')
       t = max(abs([t1 t2]));
       if abs(t) < 1e-6, 
          sout ='nsec.';
          mfac= 1e-9;
       elseif abs(t) < 1e-3, 
          sout ='usec.';
          mfac= 1e-6;
       elseif abs(t) < 1, 
          sout ='msec.'; 
          mfac= 1e-3;
       elseif abs(t) < 60, 
          sout ='sec.'; 
          mfac= 1;
       elseif abs(t) < 3600
          sout = 'Min';
          mfac= 60;
       elseif abs(t) < 3600*60
          sout = 'Hrs';
          mfac= 3600;
       else
          sout ='sec.'; 
          mfac = 1;
       end; % if
       a = t1/mfac;
       b = t2/mfac;
       

   else
     ['unrecognized mode in sec2str']
   end;

 end;
% end function







