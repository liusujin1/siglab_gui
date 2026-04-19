  function s_out=pullstr(s_in,index)
% function s_out=pullstr(s_in,index)
% Returns a sub-string from a longer string using the tilde delimiter
% Dick Benson, DSP Technology
   d=findstr(s_in,'~');   % ~ is string delimiter
   if index == 1
       start=1;
   else
       start= d(index-1)+1;
   end;
   stop  = d(index)-1;
   s_out = s_in(start:stop); 
% end function

