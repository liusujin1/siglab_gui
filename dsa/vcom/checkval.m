  function ret = checkval(in_handle,minv,maxv,oldv,fmt,quant) 
% function ret = checkval(in_handle,minv,maxv,oldv,fmt,quant)
% checks numerical string entry to insure proper range and format
% quant(ization) is optional arg.
% Dick Benson DSP Technology
  val = s2n(get(in_handle,'string'));
  if ~isempty(val)
      if val < minv
         ret=minv;
         fprintf(1,'%c',7); % beep
      elseif val > maxv
         ret=maxv;
         fprintf(1,'%c',7); % beep
      else
         ret=val;
      end;
  else
      ret = oldv; 
      % do a beep... 
      fprintf(1,'%c',7);  % beep
  end;
  
  if nargin == 6
     % quantize return to steps of quant
     ret=quant*round(ret/quant);
  end;
  
  set(in_handle,'string',ftoa(fmt,ret));
% end function 

