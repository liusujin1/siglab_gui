  function s = strpack(StrLen,in1,in2,in3,in4);
% function s = strpack(StrLen,in1,in2,in3,in4);  
% Glue strings in1..4 together with delimiter and make final string
% StrLen in length
% Dick Benson DSP Technology
   
   s = in1; % gotta have at least one string ...
   if nargin >=3, s = [s,'~',in2]; end;
   if nargin >=4, s = [s,'~',in3]; end;
   if nargin >=5, s = [s,'~',in4]; end;
   if length(s) < StrLen
      for i = length(s):StrLen-1,
        s=[s,'~'];   % Bulk up with delimeters
      end; 
   else 
      temp=s(1:StrLen-1);
      temp(StrLen) = '~';
      % this is not bullet proof, could loose delimeters
      s=temp;
   end; 
%end function


